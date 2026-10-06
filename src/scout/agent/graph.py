"""The conversation graph: clarify, search, recommend, and the follow-up tools."""
from dataclasses import dataclass, field
from typing import Any, Callable

from langgraph.graph import END, START, StateGraph

from scout.agent.clarify import clarify, draft_profile, pick_category
from scout.agent.followup import act
from scout.agent.history import assistant, compact, history_text, last_user_text, user_numbers
from scout.agent.lookup import ImageAnswer, Lookup
from scout.agent.state import ChatState
from scout.crawl import CrawlReport
from scout.llm.client import LLMOutputError, LLMUnavailable
from scout.needs import Needs
from scout.picks.pipeline import PicksResult
from scout.picks.rank import Card
from scout.picks.write import numbers_in
from scout.profiles import CategoryProfile
from scout.trace import NoTracer

MIN_STORED = 10  # after the first search of a thread, fewer stored matches than this start a new search

AFTER_ACT = {"answer": END, "exclude": "recommend", "restore": "recommend",
             "change_needs": "clarify", "more": "search", "recheck": "recommend", "new_search": "reset", "stop": END}


@dataclass
class Deps:
    llm: Any
    profile_names: Callable[[], list[str]]
    load_profile: Callable[[str], CategoryProfile]
    count: Callable[[CategoryProfile, Needs, set[int]], int]
    crawl: Callable[[CategoryProfile, Needs, int], CrawlReport]
    # profile, needs, excluded product IDs, the cards that stay without a new check, the cards to check first
    picks: Callable[[CategoryProfile, Needs, set[int], list[Card], list[Card]], PicksResult]
    cards: Callable[[list[int]], list[Card]]
    eliminate: Callable[[str, list[int], str], None]
    eliminated: Callable[[str], set[int]]
    restore: Callable[[str, list[int]], None]
    say: Callable[[str], None] = print
    tracer: Any = field(default_factory=NoTracer)
    # The lookup tools. Without page_texts the model has only the cards.
    page_texts: Callable[[list[int]], dict] | None = None
    query: Callable[[CategoryProfile, Needs], list[Card]] | None = None
    read_images: Callable[[list[str], str], Any] | None = None
    search_text: Callable[..., list] | None = None  # (question, scope, k) -> hits
    save_draft: Callable[[CategoryProfile], Any] | None = None  # without it, no new category is drafted
    # Reads the shop reviews of the picks after they are shown (phase 3 Task 5). Gives the report text, or "".
    read_reviews: Callable[[list[dict]], str] | None = None
    # The pros and complaints of the picks from their reviews (phase 3 Task 7): (profile, [(number, listing_id)],
    # language) -> (text, {listing_id: checked pros and complaints with their sources})
    opinions: Callable[[CategoryProfile, list[tuple[int, int]], str], tuple[str, dict]] | None = None


def crawl_text(report: CrawlReport, terms: str, page: int) -> str:
    pages = f"result page {page}" if report.search_pages <= 1 else \
        f"result page {page}, and page 1 sorted by rating and by sales"
    line = (f"Searched amazon.se for '{terms}' ({pages}): {report.candidates} results, "
            f"{report.off_topic} left out by title, {report.over_budget} over budget, "
            f"{report.known} already stored (rating and review count updated), {report.saved} new products saved.")
    return "\n".join([line, *report.notes])


def higher_budget(new: float | None, old: float | None) -> bool:
    """True when the new budget lets in products that the old search left out. None means no budget."""
    if old is None:
        return False
    return new is None or new > old


def lower_budget(new: float | None, old: float | None) -> float | None:
    if new is None or old is None:
        return old if new is None else new
    return min(new, old)


def thread_of(config) -> str:
    return config["configurable"]["thread_id"]


def action_of(state) -> str | None:
    return (state.get("route") or {}).get("action")


def language_of(state) -> str:
    return f"the language of this message from the shopper: {last_user_text(state)[:200]!r}"


def build_graph(deps: Deps, checkpointer=None):
    llm = deps.llm

    def guarded(name, fn):
        def node(state, config):
            with deps.tracer.span(name) as span:
                try:
                    return fn(state, config)
                except LLMUnavailable as exc:
                    text = f"I cannot finish this step now. {exc}"
                except LLMOutputError as exc:
                    text = f"The model did not give a usable reply for this step. {exc} You can say it in other words."
                except Exception as exc:  # a browser or database problem must not end the conversation
                    span.update(level="ERROR", status_message=repr(exc))
                    text = (f"Something went wrong in the step '{name}' ({type(exc).__name__}: {exc}). "
                            "Your needs are kept. You can try again.")
                return {"messages": [assistant(text)], "route": {"action": "stop"}}
        return node

    def compact_node(state, config):
        with deps.tracer.span("compact"):
            try:
                return compact(state, llm)
            except (LLMUnavailable, LLMOutputError):
                return {}  # the model then sees all messages since the last summary

    def needs_of(state) -> Needs:
        return Needs.model_validate(state.get("needs") or {})

    def clarify_node(state, config):
        needs, history = needs_of(state), history_text(state)
        if needs.category is None:
            names = deps.profile_names()
            choice = pick_category(llm, history, [deps.load_profile(n) for n in names])
            if choice.category is None and choice.new_category and deps.save_draft:
                profile = draft_profile(llm, choice.new_category, history, names)
                deps.save_draft(profile)
                choice.category = profile.category
                # A status line, not a message: the shopper does not need the draft in the conversation
                deps.say(f"New category drafted: {profile.category}. Kinds: "
                         f"{', '.join(k.label for k in profile.kinds)}. Facts that it compares: "
                         f"{', '.join(a.label for a in profile.attributes)}.")
            if choice.category is None:
                return {"phase": "clarify", "route": {}, "messages": [assistant(
                    f"I can search these categories now: {', '.join(names)}. Which one do you want?")]}
            needs.category = choice.category
        reply = clarify(llm, deps.load_profile(needs.category), needs, history, user_numbers(state))
        update = {"needs": reply.needs.model_dump(mode="json"), "route": {}}
        if reply.ready:
            return {**update, "phase": "search"}
        return {**update, "phase": "clarify", "messages": [assistant(reply.question)]}

    def search_node(state, config):
        needs = needs_of(state)
        profile = deps.load_profile(needs.category)
        terms = needs.search_terms(profile)
        searches = dict(state.get("searches") or {})
        last = searches.get(terms)
        conditions = f"Searching with: {needs.text() or 'no conditions'}."
        if last is None:
            page, why = 1, "the first search for these words in this conversation"
        elif higher_budget(needs.budget, last["budget"]):
            page, why = 1, "the budget is higher than in the earlier search"
        elif action_of(state) == "more":
            page, why = last["page"] + 1, "you asked for more"
        elif deps.count(profile, needs, deps.eliminated(thread_of(config))) < MIN_STORED:
            page, why = last["page"] + 1, f"fewer than {MIN_STORED} stored products match"
        else:
            return {"phase": "recommend", "messages": [assistant(
                f"{conditions} I searched amazon.se for '{terms}' earlier in this conversation, and enough "
                "stored products match, so I use them and check each pick again on amazon.se.")]}
        deps.say(f"Searching amazon.se for '{terms}', result page {page} ({why}). This can take a few minutes.")
        report = deps.crawl(profile, needs, page)
        budget = needs.budget if page == 1 else lower_budget(needs.budget, last["budget"])
        searches[terms] = {"page": page, "budget": budget}
        return {"phase": "recommend", "searches": searches,
                "messages": [assistant(f"{conditions}\n{crawl_text(report, terms, page)}")]}

    def marked_cards(picks: list[dict]) -> list[Card]:
        """The cards of the picks from the database, with the size doubts that the picks step found."""
        doubtful = {p["listing_id"]: set(p.get("doubtful", [])) for p in picks}
        cards = deps.cards([p["listing_id"] for p in picks])
        for card in cards:
            card.attributes = {k: ({**v, "doubtful": True} if k in doubtful[card.listing_id] else v)
                               for k, v in card.attributes.items()}
        return cards

    def recommend_node(state, config):
        needs, action = needs_of(state), action_of(state)
        excluded = set(deps.eliminated(thread_of(config)))
        shown = state.get("picks", [])
        kept: list[dict] = []
        first: list[Card] = []
        if action == "more":
            excluded |= set(state.get("seen", []))
        elif action in ("exclude", "restore"):
            kept = [p for p in shown if p["product_id"] not in excluded]
        if action == "restore":  # the products that came back are checked first
            first = deps.cards((state.get("route") or {}).get("restored", []))
        elif action == "recheck":
            first = marked_cards(shown)
        result = deps.picks(deps.load_profile(needs.category), needs, excluded, marked_cards(kept), first,
                            language_of(state))
        if kept:  # the kept picks were not fetched again, so their notes come from the state
            result.card_notes = {**{p["listing_id"]: p["note"] for p in kept if p.get("note")}, **result.card_notes}
            result.notes += [p["note"] for p in kept if p.get("note")]
        picks = [{"listing_id": c.listing_id, "product_id": c.product_id, "title": c.title,
                  "doubtful": [k for k, v in c.attributes.items() if v.get("doubtful")],
                  "note": result.card_notes.get(c.listing_id)} for c in result.chosen]
        if action == "more" and not picks:  # keep the picks that the shopper saw
            return {"phase": "picks", "route": {}, "messages": [assistant(
                "No other stored product meets these needs. You can loosen a condition, or ask for more again.")]}
        ids = [p["product_id"] for p in picks]
        seen = ids if action not in ("more", "exclude", "restore", "recheck") else [*state.get("seen", []), *ids]
        text = result.render() if picks else ("No stored product meets these needs. You can loosen a condition, "
                                              "or ask me to search more.")
        return {"picks": picks, "seen": sorted(set(seen)), "phase": "picks", "route": {"new_picks": bool(picks)},
                "messages": [assistant(text)]}

    def reviews_node(state, config):
        # Only after the picks of this turn: a failed step or "no other product" keeps the old picks
        if not (state.get("route") or {}).get("new_picks"):
            return {}
        messages, update = [], {}
        if deps.read_reviews is not None:
            text = deps.read_reviews(state["picks"])
            if text:
                messages.append(assistant(text))
        if deps.opinions is not None:
            known = dict(state.get("opinions") or {})
            # Picks kept from an earlier turn have their summary already
            todo = [(n, p["listing_id"]) for n, p in enumerate(state["picks"], 1) if str(p["listing_id"]) not in known]
            try:
                if todo:
                    text, found = deps.opinions(deps.load_profile(needs_of(state).category), todo, language_of(state))
                    if text:
                        messages.append(assistant(text))
                    update["opinions"] = {**known, **{str(k): v for k, v in found.items()}}
            except Exception as exc:  # the report of the reviews that were read must still reach the shopper
                messages.append(assistant(f"No summary of the reviews now ({type(exc).__name__}: {exc})."))
        return {**update, "messages": messages} if messages else update

    def act_node(state, config):
        picks, removed = state.get("picks", []), list(state.get("removed", []))
        needs = needs_of(state)
        allowed = user_numbers(state) | numbers_in(needs.text())
        cards = marked_cards(picks)
        lookup = None
        if deps.page_texts and needs.category:
            lookup = Lookup(deps.load_profile(needs.category), needs, cards, deps.page_texts,
                            deps.query or (lambda profile, n: []),
                            deps.read_images or (lambda urls, q: ImageAnswer(found=False)), deps.search_text)
        result = act(llm, picks, cards, removed, history_text(state), allowed, last_user_text(state),
                     lookup=lookup)
        if result is None:
            return {"route": {"action": "stop"}, "messages": [assistant(
                "I could not write a reply whose numbers and actions agree with the records, so I changed nothing. "
                "You can say it in other words.")]}
        route = {"action": result.action, "tools": result.tools, "lookups": result.lookups, "pick_numbers": [],
                 "restored": []}
        thread, messages = thread_of(config), []
        if result.remove:  # Python says what it removed, so the text is always true
            chosen = [picks[n - 1] for n in result.remove.picks]
            deps.eliminate(thread, [p["product_id"] for p in chosen], result.remove.reason)
            removed += [{k: p[k] for k in ("product_id", "listing_id", "title")} for p in chosen]
            route["pick_numbers"] = result.remove.picks
            messages.append(assistant(f"Removed: {'; '.join(p['title'][:60] for p in chosen)}. "
                                      "They stay out for the rest of this conversation."))
        if result.restore:
            back = [r for r in removed if r["product_id"] in set(result.restore.product_ids)]
            deps.restore(thread, [r["product_id"] for r in back])
            removed = [r for r in removed if r not in back]
            route["restored"] = [r["listing_id"] for r in back]
            messages.append(assistant(f"Brought back: {'; '.join(r['title'][:60] for r in back)}."))
        if result.reply:
            messages.append(assistant(result.reply.text))
        return {"route": route, "removed": removed, "messages": messages}

    def reset_node(state, config):
        return {"needs": {}, "picks": [], "seen": [], "removed": [], "phase": "clarify"}

    def unless_stopped(state):
        return END if action_of(state) == "stop" else "recommend"

    graph = StateGraph(ChatState)
    graph.add_node("compact", compact_node)
    for name, fn in [("clarify", clarify_node), ("search", search_node), ("recommend", recommend_node),
                     ("act", act_node), ("reset", reset_node), ("reviews", reviews_node)]:
        graph.add_node(name, guarded(name, fn))
    graph.add_edge(START, "compact")
    graph.add_conditional_edges("compact", lambda s: "act" if s.get("phase") == "picks" else "clarify",
                                ["clarify", "act"])
    graph.add_conditional_edges("clarify", lambda s: "search" if s.get("phase") == "search" else END,
                                ["search", END])
    graph.add_conditional_edges("search", unless_stopped, ["recommend", END])
    graph.add_edge("recommend", "reviews")
    graph.add_edge("reviews", END)
    graph.add_conditional_edges("act", lambda s: s["route"]["action"], AFTER_ACT)
    graph.add_edge("reset", "clarify")
    return graph.compile(checkpointer=checkpointer)
