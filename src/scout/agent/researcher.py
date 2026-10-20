"""The candidate researcher (plan 2026-10-20-candidate-researcher.md): a subagent that researches one product in its
own context and gives the main agent a short file. One researcher for each product, all at the same time.

Python keeps a point of a file only when its quote is in a tool result that this researcher got."""
import contextvars
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from scout.agent.loop import (MAX_PRODUCT_PAGES, MAX_REPORT_CHARS, MAX_WEB_PAGES, MAX_WEB_SEARCHES, REPORT_LIMITS,
                              FindMissing, ReadPage, ReadProduct, SameProduct, SearchText, SearchWeb, SummarizeReviews,
                              Think, Toolbox, call_message, result_message)
from scout.llm.client import LLMOutputError, LLMUnavailable, tool_name
from scout.needs import Needs
from scout.websearch import quote_found

MAX_STEPS = 8  # model calls of one researcher; in the last one it can only finish
MAX_POINTS = 10
WEB_SEARCHES_EACH = 3  # at most, and less when the turn has fewer left: tests, forums and shops
PRODUCT_PAGES_EACH = 3  # pages of other shops (read_page), at most
ROLE = "researcher"  # config/models.yaml; without it the worker


class FilePoint(BaseModel):
    topic: Literal["need", "pro", "con", "test", "other"]
    text: str = Field(description="One short sentence")
    source: str = Field(description="Where it comes from, as the tool result names it, for example 'chunk 812', "
                                    "'review R2ABC', 'page' or a web address")
    quote: str = Field(description="The exact words of the source that show it, at most 30 words. Copy them; do "
                                   "not translate")


class ResearchFile(BaseModel):
    """Finish: the file of the product for the main assistant."""
    fit: Literal["fits", "does not fit", "unclear"] = Field(description="Does the product meet the needs?")
    fit_reason: str = Field(description="One or two sentences: which needs it meets, fails or leaves unclear")
    points: list[FilePoint] = Field(description=f"At most {MAX_POINTS}, the most useful first")
    missing: str = Field("", description="What you could not find out, in one sentence")


SYSTEM = f"""You research one product for a shopping assistant. The assistant decides the picks; you find the facts.

- Check the product against each need of the shopper: read its stored page (read_product), look for a missing
  value (find_missing), and search its stored text (search_text).
- Search the web for this model with short words (the brand and the model, and for tests one word such as test,
  recension or review), unless the wish says not to. Use the sources of search_web: tests (test sites), forums
  (owner views) and shops (Prisjakt and Swedish shops). On a shop or Prisjakt page of the same model, read_page
  stores its buyer reviews. When read_page stores it as another product, and the brand and the model are the
  same, call same_product. Then sum up all the buyer reviews (summarize_reviews).
- A test or a review counts only when it is about the same model and variant (size, colour, finish).
- Finish with research_file. Each point needs its source and a quote: the exact words of a tool result that you got.
  Python removes a point whose quote is not in your tool results. Write the points in English.
- Say "unclear" when the facts do not show if the product fits. Do not guess.
- You have at most {MAX_STEPS} steps. Call several tools in one step when they do not depend on each other."""

TOOLS = [ReadProduct, SearchText, FindMissing, SummarizeReviews, SearchWeb, ReadPage, SameProduct, Think]


def role_of(llm) -> str:
    roles = getattr(getattr(llm, "config", None), "roles", None)
    return ROLE if roles is None or ROLE in roles else "worker"


def scoped(call: BaseModel, listing_id: int) -> BaseModel:
    """The call for the own product of the researcher only."""
    if isinstance(call, (ReadProduct, FindMissing, SummarizeReviews, SearchText, SearchWeb)):
        return call.model_copy(update={"ids": [listing_id]})
    if isinstance(call, SameProduct):  # it joins another listing to its own product only
        other = call.same_as if call.id == listing_id else call.id
        return call.model_copy(update={"id": other, "same_as": listing_id})
    return call


@dataclass
class Researched:
    listing_id: int
    title: str
    file: ResearchFile | None = None
    points: list[FilePoint] = field(default_factory=list)  # the points with a quote that Python found
    dropped: int = 0
    error: str = ""
    used: dict = field(default_factory=dict)  # what this researcher used: web searches, pages, credits, steps
    update: dict = field(default_factory=dict)  # the state changes of its tools (the web searches)
    messages: list[dict] = field(default_factory=list)  # its transcript, for the evaluation


def facts_of(card) -> str:
    """The product line and its stored facts: a source that a point can quote."""
    return (f"Your product: id {card.listing_id}, {card.title[:200]}" + (f", model {card.model}" if card.model else "")
            + (f", brand {card.brand}" if card.brand else "") + "\nIts stored facts: "
            + (", ".join(f"{name} {(value or {}).get('value')}" for name, value in card.attributes.items()) or "none"))


def context_of(state: dict, card, want: str) -> str:
    needs = Needs.model_validate(state.get("needs") or {})
    return "\n".join([
        f"The needs of the shopper: {needs.text() or 'none recorded'}" + (
            f"; budget {needs.budget}" if needs.budget is not None else ""),
        f"The wish of the assistant: {want.strip() or 'check the needs; tests, problems and opinions of owners'}",
        facts_of(card),
    ])


def research_one(deps, state: dict, thread: str, card, want: str, budget: dict) -> Researched:
    """One researcher. budget: the used counters to start from, so the limits of the turn give its own part."""
    done = Researched(card.listing_id, card.title)
    box = Toolbox(deps, state, thread, browser=False)
    box.used = dict(budget)
    history: list[dict] = []
    try:
        steps(done, deps, box, card, context_of(state, card, want), history)
    except Exception as exc:  # what it used still counts for the turn
        done.error = f"the researcher failed ({type(exc).__name__}: {exc})"
    if done.file is None and not done.error:
        done.error = "the researcher did not finish"
    done.used |= {k: box.used.get(k, 0) - budget.get(k, 0)
                  for k in ("web_searches", "web_pages", "web_credits", "product_pages")}
    if box.used.get("web_unavailable") and not budget.get("web_unavailable"):
        done.used["web_unavailable"] = box.used["web_unavailable"]
    done.update = {k: v for k, v in box.update.items() if k in ("web_searches", "pages_read")}
    done.messages = history
    return done


def steps(done: Researched, deps, box: Toolbox, card, context: str, history: list[dict]) -> None:
    # The needs and the wish are not a source: a point cannot quote them
    seen = [facts_of(card)]
    role = role_of(deps.llm)
    for step in range(MAX_STEPS):
        tools = [ResearchFile] if step == MAX_STEPS - 1 else TOOLS + [ResearchFile]
        try:
            calls = deps.llm.tools(role, SYSTEM, context, tools, name="Researcher", history=history)
        except (LLMUnavailable, LLMOutputError) as exc:
            done.error = f"the researcher model failed ({type(exc).__name__}: {exc})"
            break
        done.used["steps"] = step + 1
        ids = [f"r{card.listing_id}_{step}_{n}" for n in range(len(calls))]
        history.append(call_message(calls, ids))
        final = next((c for c in calls if isinstance(c, ResearchFile)), None)
        if final is not None:
            history += [result_message(i, "done.") for i in ids]
            done.file = final
            for point in final.points[:MAX_POINTS]:
                if any(quote_found(point.quote, text) for text in seen):
                    done.points.append(point)
                else:
                    done.dropped += 1
            break
        for call, call_id in zip(calls, ids):
            call = scoped(call, card.listing_id)
            if isinstance(call, SearchWeb):  # it takes the database lock inside, only for its database parts
                report = box.run(call)
            else:  # the researchers share one database connection
                with deps.db_lock:
                    report = box.run(call)
            report = report[:REPORT_LIMITS.get(tool_name(type(call)), MAX_REPORT_CHARS)]
            seen.append(report)
            history.append(result_message(call_id, report))


def file_text(r: Researched) -> str:
    lines = [f"[id {r.listing_id}] {r.title[:100]}"]
    if r.file is None:
        return "\n".join(lines + [f"- not researched: {r.error}"])
    lines.append(f"Fit: {r.file.fit}. {r.file.fit_reason}")
    for p in r.points:
        lines.append(f"- {p.topic}: {p.text} \"{' '.join(p.quote.split())}\" ({p.source})")
    if r.file.missing:
        lines.append(f"Missing: {r.file.missing}")
    if r.dropped:
        lines.append(f"({r.dropped} point(s) left out: the quote was not in the tool results of the researcher.)")
    return "\n".join(lines)


def research_all(deps, state: dict, thread: str, cards: list, want: str, used: dict) -> list[Researched]:
    """One researcher for each card, all at the same time. Each gets an equal part of the web searches and pages
    that are left in the turn."""
    searches_left = max(0, MAX_WEB_SEARCHES - used.get("web_searches", 0))
    pages_left = max(0, MAX_WEB_PAGES - used.get("web_pages", 0))
    each_searches = min(WEB_SEARCHES_EACH, searches_left // len(cards))
    each_pages = pages_left // len(cards)
    each_products = min(PRODUCT_PAGES_EACH, max(0, MAX_PRODUCT_PAGES - used.get("product_pages", 0)) // len(cards))
    budget = {"web_searches": MAX_WEB_SEARCHES - each_searches, "web_pages": MAX_WEB_PAGES - each_pages,
              "product_pages": MAX_PRODUCT_PAGES - each_products}
    if used.get("web_unavailable"):  # the researchers do not try a back end that failed in this turn
        budget["web_unavailable"] = used["web_unavailable"]
    with ThreadPoolExecutor(len(cards)) as pool:
        futures = [pool.submit(contextvars.copy_context().run, research_one, deps, state, thread, card, want, budget)
                   for card in cards]
        found = []
        for card, future in zip(cards, futures):
            try:
                found.append(future.result())
            except Exception as exc:  # one researcher must not lose the files of the others
                found.append(Researched(card.listing_id, card.title,
                                        error=f"the researcher failed ({type(exc).__name__}: {exc})"))
        return found
