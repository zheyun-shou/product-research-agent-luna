"""The agent loop (design 2026-10-06-agent-loop-design.md): the chat model decides and calls tools; Python runs the
tools, keeps the fetching polite, and checks the floors and the live price before the picks show. The facts of
the answers are graded in the evaluation (scout grade)."""
import itertools
import json
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Literal
from urllib.parse import urlparse

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from scout.agent.history import assistant, compact, history_text, user_numbers
from scout.agent.state import ChatState
from scout.lang import t, turn_lang
from scout.llm.client import LLMOutputError, LLMUnavailable, tool_name
from scout.needs import ConditionSpec, Needs, check_needs
from scout.extract.variants import size_page
from scout.picks.rank import Card
from scout.picks.wishes import Ranked, rank_by_wishes
from scout.picks.write import attribute_text
from scout.profiles import CategoryProfile, ProfileAttribute, attribute_errors
from scout.trace import NoTracer

MAX_TOOL_CALLS = 25  # for each turn; set again from the hidden-target test (design section 10)
MAX_PRODUCT_PAGES = 30
MAX_REVIEW_PAGES = 30
MAX_WEB_PAGES = 120  # pages that the worker judges; a search gives up to 20, so 6 searches (5 picks and 1 more)
MAX_WEB_SEARCHES = 10  # a new search costs one Tavily credit; SearXNG is free but asks other engines
MAX_RESEARCH = 5  # products in one research_products call
MAX_GATE_ROUNDS = 3
MAX_REPORT_CHARS = 6_000  # of one tool report in the prompt
SEARCH_DETAILS = 8  # new product pages for each search_shop call
MAX_READ_CHARS = 5_000  # of the text of one product in read_product
# Three products must fit in one report: each text is cut at MAX_READ_CHARS, the title and the question at 200
REPORT_LIMITS = {"read_product": 3 * (MAX_READ_CHARS + 300) + 500,
                 "search_web": 60_000,  # up to websearch.MAX_CHAT_PARTS parts of about 1,000 characters
                 "research_products": 20_000,  # 5 products with up to 6 points and their quotes
                 "research_candidates": 20_000}  # 5 files with up to 10 points

# The line of the prompt about research_candidates; a run without the researchers leaves it out (eval-hidden)
RESEARCH_CANDIDATES_TEXT = """- When you have the candidates that you may recommend, call research_candidates with them (at most 5). A
  researcher for each one reads its page, its reviews and the web in its own context and gives you a short file.
  This keeps your context short. Then read more yourself only where a file says that something is unclear.
"""

SYSTEM = """You are product-scout, a shopping research assistant. You help the shopper choose products. The shopper
is in Sweden. You find candidates in shops, check them against the shopper's needs, read what buyers say, and give a
few picks with the reasons. You work with tools. Python runs them and gives you the results.

How to work:
- First understand the needs. Before the first search, ask the shopper in one short message when they did not say
  what changes the picks most: the budget, and the main use or the must-haves (for example the colour, the place,
  the power supply, the type). Ask once, then search with what you know. Do not ask when the shopper says to search
  now. Record the needs with update_needs, with only numbers that the shopper wrote.
- find_products lists the stored candidates that meet conditions. It does not choose for you. Look there first:
  products from earlier searches are stored, and reading them costs no shop pages. Record the soft wishes that are
  not numbers (quiet, white, a modern look) as preferences: find_products orders by them and shows which ones the
  stored text of a candidate meets or goes against. Say in a pick which wishes it meets, with the source.
- Search before you recommend. search_shop reads amazon.se. Use Swedish search words. Read more results, more
  pages or search with other words when the stored and the new candidates are weak or too few.
- Look things up before you guess: read_product, search_text, read_images, find_missing. find_products also lists
  candidates that do not show a value of a condition. Do not leave out such a candidate only because the value is
  missing: look for it first (find_missing, read_product).
- similar_products finds stored products near a product, near a soft wish that is not a number, or both (for example
  "like pick 2 but quieter", "a modern look"). Check that a near product meets the needs before it is a pick.
- The context lists what you remember about the shopper from earlier conversations. Use it, and do not ask again
  for what you know, but say what you use, because it can be old. Keep with remember what the shopper says that will
  still be true in a later conversation: a size of the space, a brand or a feature that they want or do not want.
  When the shopper says that a memory is wrong or asks to forget it, use forget.
- Read the buyers' reviews of the products that you want to recommend (read_reviews, then summarize_reviews).
{research_candidates}- To research products on the web only, use research_products: one researcher for each
  product searches at the same time, and you get short points with quotes and addresses. Use short search words:
  the brand and model and one word such as test, recension, Testbericht, anmeldelse or review. A long question in a
  sentence finds manuals and shops. Put what you want to learn in want (for example "is it quiet").
- search_web with sources tests, forums or shops searches only the sites of a list: test sites, forums, or
  Prisjakt and Swedish shops. After a search of shops, read_page on the page of the same model stores its buyer
  reviews (Prisjakt and Elgiganten give many). Buyer reviews of other shops count as buyer reviews; keep them apart
  from tests.
- search_web gives the best parts of the pages of one search. Use it for other questions and for more detail: with
  ids, about those products; without ids, which models testers recommend. A model that a test praises may not be sold in Sweden, but look for it: search_shop with its brand
  and model, and search_web with the model and a word such as pris or köp for the pages of other shops (read_page).
  Say when you could not find it in a shop.
- read_page reads the product page of another shop (for example one that search_web found) and stores the product.
  It can be a pick. It joins the same product of amazon.se when the GTIN, or the brand and the model, are the same.
  When it lists stored products of the same brand, judge from the titles and the models if one is the same
  product, and then call same_product. Its reviews are the ones that its page shows.
- A test or a forum view is about a pick only when it is about the same model and variant (size, colour, finish).
  When it is about another variant, say so, or do not use it.
- When you say how many candidates fit, count them in the tool results. Name the shop of each price that is not
  from amazon.se.
- Say clearly when data is missing, doubtful or in conflict, and what you did about it. You can leave out a
  candidate whose data cannot be right; say why.
- Use think to reason before a conclusion, mainly before show_picks: list the candidates that fit and count
  them, check each claim against the tool result that it comes from (the site, the number, the variant), and note
  what is still missing. The shopper does not see it.
- show_picks gives the final picks, best first. Usually 5. Fewer when fewer fit well; then say why.
- remove_products and restore_products change which products the shopper has rejected in this conversation.

Rules:
- A budget is a hard limit. Price does not rank the picks, unless the shopper asks for it (for example good value
  for money).
- Never add shipping, duty or moms that the shopper did not give. For a currency conversion, state the rate.
- Summarize reviews in qualitative words ("most buyers", "a few"), never as counts.
- Each number that you write is a value from a tool result, a number that the shopper wrote, or your own
  calculation. Say when a number comes from a test or a forum, and name the site.
- Before show_picks, Python checks: a shop was searched or a shop page was read in this conversation, the reviews
  of each pick were read (good and bad, or the reviews on the page of another shop), and it had a web search for
  tests (research_candidates, research_products or search_web, in the last 7 days), unless the shopper does not want
  that (update_needs with web_tests false). Python opens each pick again for the price and the stock now.
- Reply in the language of the shopper's last message."""


def system_prompt(researchers: bool = True) -> str:
    return SYSTEM.replace("{research_candidates}", RESEARCH_CANDIDATES_TEXT if researchers else "")


def work_tools(researchers: bool = True) -> list:
    return [t for t in WORK_TOOLS if researchers or t is not ResearchCandidates]


# ---- tools ----

class UpdateNeeds(BaseModel):
    """Record the shopper's needs. Give the full needs each time, not only the change."""
    category: str | None = Field(None, description="One of the categories that the context lists. When none of "
                                 "them has the product: a short new English name, lowercase with underscores "
                                 "(for example coffee_grinder). Python then drafts a profile for it")
    kind: str | None = Field(None, description="One of the kinds of the category, exactly as the context writes it")
    conditions: list[ConditionSpec] = Field(default_factory=list)
    budget: float | None = Field(None, description="SEK")
    budget_note: str | None = Field(None, description="A budget in another currency: the amount, the currency, "
                                                      "the rate used")
    preferences: list[str] = Field(default_factory=list, description="Soft wishes in a few words each, for "
                                   "example quiet or white. find_products orders the candidates by them")
    web_tests: bool = Field(True, description="False only when the shopper does not want web searches for tests")


class SearchShop(BaseModel):
    """Search amazon.se and store the new products (up to 8 product pages). It reads the default order of the shop,
    and the worker reads the best matches first. When more results fit, the result says so: call again with the
    same words and page to read the next ones, without loading the search pages again. Takes one or two minutes."""
    words: str = Field(description="Swedish search words, one phrase")
    page: int = Field(1, description="The result page, from 1")


class SearchWeb(BaseModel):
    """Search the web. The worker judges each found page (test, forum, shop; relevant or not). You get the
    parts of the relevant pages that answer the question best, with their address and date. The parts are ranked
    by the search words without the brand and the model. Give the ids of the products that the search is for: the
    relevant pages about them are stored, so search_text finds them later."""
    question: str = Field(description="The search words, for example the model and 'test'. Any language")
    ids: list[int] = Field(default_factory=list, description="Product ids that the search is for, at most 5")
    want: str = Field("", description="What you want to learn from the pages, for example 'is it quiet' or "
                                      "'problems after a year'. It ranks the parts of the text. Empty: the search "
                                      "words without the brand and the model")
    sources: Literal["any", "tests", "forums", "shops"] = Field(
        "any", description="any: the whole web. tests: the test sites of the list for the category. forums: "
                           "SweClockers, Flashback and Reddit (only SweClockers can be read; the others give a short "
                           "part). shops: Prisjakt and Swedish shops, for their buyer reviews (then read_page)")


class ResearchItem(BaseModel):
    id: int
    words: str = Field(description="Short search words: the brand and the model and one word such as test, "
                                   "recension, Testbericht or review")


class ResearchProducts(BaseModel):
    """Research the products that you want to recommend on the web, at most 5. One researcher for each product
    searches the web at the same time; the worker judges the pages and sums up the best parts. You get short points,
    each with a quote and the address of its page. The relevant pages are stored, so search_text finds them later."""
    products: list[ResearchItem] = Field(description="Each product with its own search words")
    want: str = Field("", description="What you want to learn from the pages, for example 'is it quiet'. Empty: "
                                      "tests, problems and opinions of owners")


class ResearchCandidates(BaseModel):
    """Research the products that you may recommend, at most 5. Python reads their amazon.se reviews first. Then one
    researcher for each product checks it against the needs and reads its page, its reviews and the web, all at the
    same time, in their own context. You get a short file for each product: does it fit, and points with a source
    and a quote."""
    ids: list[int] = Field(description="Product ids, at most 5")
    want: str = Field("", description="What the researchers must find out, for example 'is it quiet' or 'does it "
                                      "fit a niche of 60 cm'. Empty: the needs, tests, problems and opinions of "
                                      "owners")


class ReadPage(BaseModel):
    """Read the product page of a shop that is not amazon.se, for example a shop page that search_web found. Stores
    the product with its price, stock, specifications and the reviews that the page shows, and gives its id."""
    url: str = Field(description="The address of one product page")


class SameProduct(BaseModel):
    """Two listings in different shops are the same product: the same maker and model, perhaps written in another
    way. Python joins them, so the facts and the reviews of both belong to one product."""
    id: int
    same_as: int


class FindProducts(BaseModel):
    """List the stored products of the category that meet conditions, with their facts and ids. The order comes
    from the rating and from the preferences in the needs (never the price); each candidate shows which
    preferences moved it up, with the stored words that match best."""
    kind: str | None = None
    conditions: list[ConditionSpec] = Field(default_factory=list)
    budget: float | None = Field(None, description="Highest price in SEK, or null for the shopper's budget")
    limit: int = Field(15, description="At most 30")


class ReadProduct(BaseModel):
    """Read the full stored text of products: specifications, description, reviews on the product page. A product
    that was read in this turn is not given again; its text is in the earlier result."""
    ids: list[int] = Field(description="Product ids, at most 3")
    question: str


class SearchText(BaseModel):
    """Search the stored text of many products at once and give the 10 best matching parts."""
    question: str
    more: list[str] = Field(default_factory=list, description="The same search words in the other languages of "
                            "Swedish, English and Chinese. The stored text has many languages, and the words of "
                            "each language find more parts")
    ids: list[int] = Field(default_factory=list, description="Product ids, or empty for all of the category")
    kinds: list[Literal["specs", "description", "review", "test", "forum", "shop", "other"]] = Field(
        default_factory=list, description="test, forum, shop and other are web pages")


class SimilarProducts(BaseModel):
    """Find stored products of the category that are near one product, near a soft wish, or both, for example
    "like id 12 but quieter" (like 12, wish quiet) or "a modern look". Each product is compared as a whole, by the
    mean of all its stored text. The best matching part of each product is the reason. Removed products are left
    out. A near product does not always meet the needs; check it before it is a pick."""
    like: int | None = Field(None, description="The id of a product to be near, or null")
    wish: str = Field("", description="The soft wish in a few words, or empty")
    more: list[str] = Field(default_factory=list, description="The wish in the other languages of Swedish, English "
                            "and Chinese")
    limit: int = Field(8, description="At most 15")


class ReadImages(BaseModel):
    """Look at the product images of one product. Never read a price from an image."""
    id: int
    question: str


class FindMissing(BaseModel):
    """Look for values of number attributes that the facts of products do not have, in their stored text."""
    ids: list[int]
    attributes: list[str]


class ReadReviews(BaseModel):
    """Read the positive and the critical review pages of products on amazon.se. Needs the shopper's login."""
    ids: list[int]


class SummarizeReviews(BaseModel):
    """Qualitative pros and complaints of products from their stored reviews, each with the reviews it cites."""
    ids: list[int]


class RemoveProducts(BaseModel):
    """The shopper rejects products. They stay out for the rest of the conversation."""
    ids: list[int]
    reason: str = ""
    dislike: bool = Field(False, description="True when the reason is a lasting dislike of the shopper, for example "
                          "too loud. False for the stock, the budget or a duplicate")


class Remember(BaseModel):
    """Keep a like, a dislike or a fact of the shopper for later conversations, for example "the kitchen niche is
    60 cm wide" or "does not want Bomann". Only what the shopper said and what will still be true later. Never a
    name, an address, health or money matters. The shopper sees and deletes it on the web page."""
    kind: Literal["like", "dislike", "fact"]
    text: str = Field(description="One short sentence in the words of the shopper, with the reason when there is one")
    all_categories: bool = Field(False, description="True when it is not only for the category of the needs")


class NewAttribute(BaseModel):
    name: str = Field(description="lowercase English with underscores, for example max_weight")
    label: str
    type: Literal["number", "enum", "boolean", "text"]
    unit: str | None = Field(None, description="For a number")
    values: list[str] | None = Field(None, description="For an enum")
    kind: Literal["hard", "soft"] = Field(description="hard only for a number that a product must meet")


class UpdateProfile(BaseModel):
    """Change the profile of the category of the needs, when the shopper asks for a fact that it does not have
    (only an attribute of the profile can be a condition), or says that a fact is a must or only a wish. Python
    checks and saves the changed profile for later conversations too."""
    add: list[NewAttribute] = Field(default_factory=list)
    remove: list[str] = Field(default_factory=list, description="Attribute names")
    hard: list[str] = Field(default_factory=list, description="Attribute names that become hard (numbers only)")
    soft: list[str] = Field(default_factory=list, description="Attribute names that become soft")


class Forget(BaseModel):
    """Delete memories of the shopper, when the shopper asks or says that one is not true now."""
    ids: list[int] = Field(description="Memory ids from the context")


class RestoreProducts(BaseModel):
    """Bring back products that were removed in this conversation."""
    ids: list[int]


class Think(BaseModel):
    """Write down your reasoning, for example which candidates fit and from which tool result each claim comes.
    Nothing runs, and the shopper does not see it. It counts as a tool call."""
    note: str


class Pick(BaseModel):
    id: int
    text: str = Field(description="Why it fits, and what to watch out for")


class ShowPicks(BaseModel):
    """The final picks for the shopper, best first. Ends your work in this turn."""
    picks: list[Pick]
    note: str = Field("", description="What the shopper should know about all picks, for example why fewer than 5")


class Reply(BaseModel):
    """Your message to the shopper. Ends your work in this turn."""
    text: str


WORK_TOOLS = [UpdateNeeds, SearchShop, ResearchCandidates, ResearchProducts, SearchWeb, ReadPage, SameProduct, FindProducts, ReadProduct, SearchText,
              SimilarProducts, ReadImages, FindMissing, ReadReviews, SummarizeReviews, RemoveProducts,
              RestoreProducts, UpdateProfile, Remember, Forget, Think]
FINAL_TOOLS = [ShowPicks, Reply]
BY_NAME = {tool_name(t): t for t in WORK_TOOLS + FINAL_TOOLS}


def to_state(call: BaseModel) -> dict:
    return {"tool": tool_name(type(call)), "args": call.model_dump(mode="json")}


def from_state(item: dict) -> BaseModel:
    return BY_NAME[item["tool"]].model_validate(item["args"])


# ---- dependencies ----

@dataclass
class LoopDeps:
    llm: Any
    profile_names: Callable[[], list[str]]
    load_profile: Callable[[str], CategoryProfile]
    cards: Callable[[list[int]], list[Card]]  # listing ids -> cards, in order
    find: Callable[[CategoryProfile, Needs, set[int]], list[Card]]  # stored candidates, the removed left out
    eliminate: Callable[[str, list[int], str], None]  # thread, product ids, reason
    eliminated: Callable[[str], set[int]]
    restore: Callable[[str, list[int]], None]
    # (profile, needs, words, page, at most this many product pages) -> (report text, product pages fetched)
    search_shop: Callable[[CategoryProfile, Needs, str, int, int, list[str]], tuple[str, int]]
    # listing ids -> {listing_id: problem or ""}; opens each page again and stores it
    check_now: Callable[[list[int], Needs], dict[int, str]]
    page_texts: Callable[[list[int]], dict[int, dict]] | None = None
    search_text: Callable[..., list] | None = None
    # (like listing ids, wishes, scope, k, removed product ids) -> [Similar], one for each product; raises NoText
    similar_products: Callable[..., list] | None = None
    # (cards, wishes) -> {listing id: {wish: Verdict}}; the worker judges the nearest stored text (picks.wishes)
    judge_wishes: Callable[[list[Card], list[str]], dict] | None = None
    save_draft: Callable[[CategoryProfile], Any] | None = None  # without it, a new category is not drafted
    read_images: Callable[[list[str], str], Any] | None = None
    find_missing: Callable[[list[Card], list[str], CategoryProfile], str] | None = None
    # -> (report, review pages, ids whose reviews cannot be read: not on amazon.se, or the shopper skipped the login)
    read_reviews: Callable[[list[int]], tuple[str, int, list[int]]] | None = None
    reviews_read: Callable[[list[int]], set[int]] = lambda ids: set()  # listings with a recent complete read
    summarize: Callable[[CategoryProfile, list[int], str], str] | None = None
    # (search words, listing ids, at most this many pages, what to learn) -> (report, pages, credits);
    # raises WebSearchUnavailable
    search_web: Callable[[str, list[int], int, str], tuple[str, int, int]] | None = None
    # ([(listing id, search words)], at most this many pages for each, what to learn) -> ResearchReport;
    # one researcher for each product at the same time (websearch.research)
    research_web: Callable[[list[tuple[int, str]], int, str], Any] | None = None
    web_searched: Callable[[list[int]], set[int]] = lambda ids: set()  # listings with a web search, last 7 days
    # (url, profile) -> (report, pages loaded, the listing id or None); stores the product of another shop
    read_page: Callable[[str, CategoryProfile], tuple[str, int, int | None]] | None = None
    same_product: Callable[[int, int], str | None] | None = None  # (id, same as) -> the problem or None
    # The memory of the shopper across conversations (Task 13). remember(kind, text, category, thread, source) -> id;
    # source is "remember" (the remember tool) or "removed" (the reason of a removed product)
    memories: Callable[[], list[dict]] = lambda: []
    remember: Callable[..., int] | None = None
    forget: Callable[[list[int]], list[int]] | None = None
    say: Callable[[str], None] = print
    lang: str = "en"  # the language of the shopper in this turn (scout.lang); the start of each turn sets it
    step: Callable[[dict], None] = lambda event: None  # the progress of a turn, for the web page
    tracer: Any = field(default_factory=NoTracer)
    # The lock of the database connection. The candidate researchers run their tools that use the database with it;
    # search_web takes it inside, only for its database parts.
    db_lock: Any = field(default_factory=threading.RLock)
    researchers: bool = True  # the research_candidates tool; eval-hidden compares runs with and without it


# ---- text for the model ----

def candidate_text(card: Card) -> str:
    attrs = ", ".join(f"{k}={attribute_text(v)}" for k, v in card.attributes.items())
    stock = {True: "yes", False: "no", None: "not known"}[card.in_stock]
    variant = f" | variant={card.variant}" if card.variant else ""
    return (f"id {card.listing_id}: {card.title}{variant} | price={card.price} {card.currency or ''} | "
            f"in_stock={stock} | rating={card.rating} | review_count={card.review_count} | {attrs or 'no attributes'}")


def ranked_text(r: Ranked) -> str:
    """A candidate of find_products, with the preferences that its stored text meets or goes against, and the
    words that show it."""
    def shown(wish: str) -> str:
        verdict = r.verdicts[wish]
        return f'{wish} ({verdict.hit.ref}: "{verdict.quote}")' if verdict.hit else wish
    text = candidate_text(r.card)
    for name, wishes in (("meets", r.moved_by()), ("against", r.against())):
        if wishes:
            text += f"\n  {name}: " + "; ".join(map(shown, wishes))
    return text


def _without_title(chunk: str) -> str:
    """A chunk starts with the line "Product: <title>"; the card already gives the title."""
    first, _, rest = chunk.partition("\n")
    return rest.strip() if first.startswith("Product:") and rest.strip() else chunk


def context_text(state: dict, deps: LoopDeps, thread: str) -> str:
    needs = Needs.model_validate(state.get("needs") or {})
    picks = deps.cards([p["listing_id"] for p in state.get("picks", [])])
    removed = state.get("removed", [])
    searches = state.get("searches") or {}
    # The kinds are given before the first update_needs, so the model writes their names right the first time
    profiles = [deps.load_profile(name) for name in deps.profile_names()]
    categories = ", ".join(f"{p.category}{' (draft)' if p.draft else ''} (kinds {', '.join(k.name for k in p.kinds)})"
                           for p in profiles)
    lines = [
        f"Categories with a profile: {categories}",
        f"Needs so far: {needs.model_dump_json(exclude_none=True)}",
    ]
    if needs.category:
        lines.append(profile_text(deps.load_profile(needs.category)))
    remembered = [m for m in deps.memories()
                  if not needs.category or m["category"] in (None, needs.category)][-MAX_MEMORIES:]
    if remembered:
        lines.append("What you remember about the shopper from earlier conversations (the shopper can delete it):\n"
                     + "\n".join(memory_text(m) for m in remembered))
    lines.append("Current picks:\n" + ("\n".join(f"{n}. {candidate_text(c)}" for n, c in enumerate(picks, 1))
                                       or "none"))
    lines.append("Removed in this conversation: " + (", ".join(f"id {r['listing_id']} {r['title'][:50]}"
                                                               for r in removed) or "none"))
    lines.append("Shop searches in this conversation: " + (", ".join(f"'{w}' up to page {s['page']}"
                                                                     for w, s in searches.items()) or "none"))
    pages = state.get("pages_read") or []
    if pages:
        lines.append("Shop pages read in this conversation: " + ", ".join(f"id {p['id']} {p['url']}" for p in pages))
    web = state.get("web_searches") or []
    if web:
        lines.append("Web searches in this conversation: " + ", ".join(f"'{q}'" for q in web))
    return "\n".join(lines)


MAX_MEMORIES = 30


def memory_text(m: dict) -> str:
    where = f" ({m['category']})" if m["category"] else ""
    return f"[memory {m['id']}] {m['kind']}{where}, {m['created_at']:%Y-%m-%d}: {m['text']}"


def memory_numbers(deps: LoopDeps, category: str | None) -> set:
    """The numbers in the memories that the remember tool kept, of the category and of all categories. They count
    as numbers that the shopper wrote, in update_needs. The titles of removed products do not count."""
    from scout.picks.write import numbers_in
    return set().union(*(numbers_in(m["text"]) for m in deps.memories()
                         if m.get("source", "remember") == "remember" and m["category"] in (None, category)))


def profile_text(profile: CategoryProfile) -> str:
    return ("Profile: kinds " + ", ".join(f"{k.name} ({k.label})" for k in profile.kinds)
            + "; number attributes " + ", ".join(f"{a.name} ({a.unit or ''})" for a in profile.attributes
                                                 if a.type == "number"))


def turn_prompt(state: dict, deps: LoopDeps, thread: str) -> str:
    """The first message of a turn: the context and the conversation at the start of the turn. The tool calls of the
    turn follow it as chat messages (turn_messages), so the model sees what it did and what came back."""
    return "\n\n".join(["The state at the start of this turn. The results of your tool calls after this message "
                        "are newer: for example a product that you removed, or needs that you recorded.",
                        context_text(state, deps, thread), "Conversation:\n" + history_text(state)])


USED_UP = "The tool calls of this turn are used up. Answer now with show_picks or reply."


def call_message(calls: list[BaseModel], ids: list[str]) -> dict:
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": i, "type": "function", "function": {"name": tool_name(type(c)), "arguments": json.dumps(
            c.model_dump(mode="json"), ensure_ascii=False)}} for c, i in zip(calls, ids)]}


def result_message(call_id: str, text: str) -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": text}


# ---- the checks of the final call (design section 8) ----

def check_final(call: BaseModel, state: dict, deps: LoopDeps, thread: str, used: dict | None = None) -> list[str]:
    """What stops the final call: the picks exist, the floors are met, and the live check of the price and the stock
    passes. The picks that pass that check are added to used["checked_now"]."""
    used = used if used is not None else dict(state.get("used") or {})
    if isinstance(call, Reply):
        return []
    assert isinstance(call, ShowPicks)
    errors: list[str] = []
    ids = [p.id for p in call.picks]
    if not ids:
        return ["show_picks has no pick. If nothing fits, tell the shopper with reply"]
    if len(set(ids)) < len(ids):
        errors.append("a product is in the picks more than one time")
    cards = {c.listing_id: c for c in deps.cards(ids)}
    errors += [f"there is no product with id {i}" for i in ids if i not in cards]
    removed = deps.eliminated(thread)
    errors += [f"id {i} was removed by the shopper in this conversation" for i in ids
               if i in cards and cards[i].product_id in removed]
    if errors:
        return errors
    # The floors (design section 5)
    if not state.get("searches"):
        # A pick from another shop has its own shop step: read_page in this conversation
        read = {p["id"] for p in state.get("pages_read") or []}
        if any(i not in read for i in ids):
            errors.append("no shop search in this conversation yet. Call search_shop first")
    if deps.read_reviews is not None:
        read = deps.reviews_read(ids)
        skipped = set(used.get("reviews_skipped", []))
        errors += [f"the reviews of id {i} were not read. Call research_candidates or read_reviews" for i in ids
                   if i not in read and i not in skipped]
    needs = Needs.model_validate(state.get("needs") or {})
    if deps.search_web is not None and needs.web_tests and not used.get("web_unavailable"):
        searched = deps.web_searched(ids)
        errors += [f"id {i} had no web search for tests yet. Call research_candidates or research_products with "
                   "its id" for i in ids if i not in searched]
    if errors:
        return errors
    # The price and the stock now, for the picks that were not checked in this turn
    checked = set(used.get("checked_now", []))
    problems = deps.check_now([i for i in ids if i not in checked], needs)
    errors += [f"id {i}: {problem}" for i, problem in problems.items() if problem]
    used["checked_now"] = sorted(checked | {i for i, problem in problems.items() if not problem})
    return errors


def picks_text(call: ShowPicks, cards: dict[int, Card], sizes: dict[int, str] | None = None, lang: str = "en") \
        -> str:
    """sizes: listing id -> the product id of the page of the size of the needs (variants.size_page). That page
    gives the price, the variant and the link of the pick."""
    from scout.sources.amazon_se.urls import product_url
    lines = []
    for n, pick in enumerate(call.picks, 1):
        card = cards[pick.id]
        size = card.variants.get((sizes or {}).get(pick.id) or "")
        price, currency = (size["price"], size["currency"]) if size else (card.price, card.currency)
        price = f"{price} {currency}" if price is not None else t("price_unknown", lang)
        rating = (t("ratings", lang, rating=card.rating, count=card.review_count) if card.rating is not None
                  else t("no_ratings", lang))
        variant = f" | {size['label']}" if size and size.get("label") else ""
        url = product_url(sizes[pick.id]) if size else card.url
        lines += [f"{n}. {card.title}", f"   {price}{variant} | {rating} | id {card.listing_id}", f"   {pick.text}",
                  f"   {url}"]
    if call.note:
        lines += ["", call.note]
    return "\n".join(lines)


# ---- running the work tools ----

# ---- progress ----

STEP_IDS = itertools.count(1)
LIMITS = {"calls": MAX_TOOL_CALLS, "product_pages": MAX_PRODUCT_PAGES, "web_searches": MAX_WEB_SEARCHES,
          "web_pages": MAX_WEB_PAGES}
STEP_DETAIL_CHARS = 160


def research_items(call: ResearchProducts) -> list[ResearchItem]:
    """The products of a research_products call with search words, each id once."""
    items, seen = [], set()
    for item in call.products:
        if item.id not in seen and item.words.strip():
            seen.add(item.id)
            items.append(item)
    return items


def step_title(call: BaseModel, lang: str = "en") -> tuple[str, str]:
    """(kind, title) of a tool call, for the progress on the web page, in the language of the shopper. The kind
    groups the steps: needs, shop, web, db, reviews."""
    if isinstance(call, UpdateNeeds):
        return "needs", t("step_needs", lang)
    if isinstance(call, SearchShop):
        return "shop", t("step_search_shop", lang, words=call.words) + (
            t("step_page", lang, page=call.page) if call.page > 1 else "")
    if isinstance(call, SearchWeb):
        return "web", t("step_search_web", lang, question=call.question)
    if isinstance(call, ResearchCandidates):
        return "web", t("step_candidates", lang, n=len(list(dict.fromkeys(call.ids))[:MAX_RESEARCH]))
    if isinstance(call, ResearchProducts):
        return "web", t("step_research", lang, n=len(research_items(call)))
    if isinstance(call, ReadPage):
        return "web", t("step_read_page", lang, host=urlparse(call.url).netloc or call.url)
    if isinstance(call, SameProduct):
        return "db", t("step_same_product", lang, id=call.id, same=call.same_as)
    if isinstance(call, FindProducts):
        return "db", t("step_find_products", lang)
    if isinstance(call, ReadProduct):
        return "db", t("step_read_product", lang, n=len(call.ids))
    if isinstance(call, SearchText):
        return "db", t("step_search_text", lang, question=call.question)
    if isinstance(call, SimilarProducts):
        near = t("step_and", lang).join(
            ([t("step_similar_product", lang, id=call.like)] if call.like is not None else [])
            + ([f"'{call.wish}'"] if call.wish.strip() else []))
        return "db", t("step_similar", lang, near=near or t("step_nothing", lang))
    if isinstance(call, ReadImages):
        return "db", t("step_images", lang, id=call.id)
    if isinstance(call, FindMissing):
        return "db", t("step_missing", lang, names=", ".join(call.attributes))
    if isinstance(call, ReadReviews):
        return "reviews", t("step_read_reviews", lang, n=len(call.ids))
    if isinstance(call, SummarizeReviews):
        return "reviews", t("step_summarize", lang, n=len(call.ids))
    if isinstance(call, RemoveProducts):
        return "needs", t("step_remove", lang, n=len(call.ids))
    if isinstance(call, RestoreProducts):
        return "needs", t("step_restore", lang, n=len(call.ids))
    if isinstance(call, Remember):
        return "needs", t(f"step_remember_{call.kind}", lang)
    if isinstance(call, UpdateProfile):
        return "needs", t("step_profile", lang)
    if isinstance(call, Forget):
        return "needs", t("step_forget", lang, n=len(call.ids))
    if isinstance(call, Think):
        return "note", t("step_think", lang)  # the note itself is not for the shopper
    return "db", tool_name(type(call))


# The first words of a tool report when the tool did nothing: a failure or a refusal
FAILED = ("failed (", "not available", "the model for this tool is not available", "not run", "not recorded",
          "record the category", "there is no product", "give a product id", "the product pages of this turn",
          "the web searches of this turn", "the web pages of this turn", "there is no memory")


def step_detail(report: str) -> tuple[str, str]:
    """(state, detail) from a tool report: its first line without the tool name, cut short."""
    lines = report.split(": ", 1)[-1].strip().splitlines()
    text = lines[0] if lines else ""
    failed = text.startswith(FAILED)
    if len(text) > STEP_DETAIL_CHARS:
        text = text[:STEP_DETAIL_CHARS - 1].rstrip() + "…"
    return ("fail" if failed else "done"), text


class Toolbox:
    def __init__(self, deps: LoopDeps, state: dict, thread: str, browser: bool = True):
        self.deps, self.state, self.thread, self.browser = deps, state, thread, browser
        self.needs = Needs.model_validate(state.get("needs") or {})
        self.used = dict(state.get("used") or {})
        self.update: dict = {}

    def current(self, key: str) -> list:
        """The value after the earlier calls of this round."""
        return list(self.update[key] if key in self.update else self.state.get(key) or [])

    def profile(self) -> CategoryProfile | None:
        return self.deps.load_profile(self.needs.category) if self.needs.category else None

    def cards(self, ids: list[int]) -> tuple[list[Card], list[int]]:
        found = self.deps.cards(ids)
        known = {c.listing_id for c in found}
        return found, [i for i in ids if i not in known]

    def run(self, call: BaseModel) -> str:
        name = tool_name(type(call))
        try:
            return f"{name}: " + getattr(self, name)(call)
        except (LLMUnavailable, LLMOutputError) as exc:
            return f"{name}: the model for this tool is not available now ({exc})."
        except Exception as exc:  # a browser, database or index problem: the chat model gets it as a result
            return f"{name}: failed ({type(exc).__name__}: {exc})."

    def update_needs(self, call: UpdateNeeds) -> str:
        names = self.deps.profile_names()
        category = call.category or self.needs.category
        drafted = ""
        if category and category not in names and self.deps.save_draft is not None:
            from difflib import get_close_matches

            from scout.agent.clarify import draft_profile
            near = get_close_matches(category, names, n=1, cutoff=0.8)  # a typo, or a singular of a plural
            if near:
                return f"'{category}' is not a category. Did you mean {near[0]}? Use one of: {', '.join(names)}."
            profile = draft_profile(self.deps.llm, category, history_text(self.state), names)
            if re.fullmatch(r"[a-z][a-z0-9_]{1,40}", category):  # the name that the chat model will use again
                profile = profile.model_copy(update={"category": category})
            self.deps.save_draft(profile)
            category = profile.category
            drafted = (f"New category {category}: Python saved a draft profile that a person has not checked. "
                       "Tell the shopper that this category is new. ")
        if category not in self.deps.profile_names():
            return f"'{category}' is not a category. Use one of: {', '.join(names)}."
        needs = Needs(category=category, **call.model_dump(exclude={"category"}))
        profile = self.deps.load_profile(category)
        errors = check_needs(needs, profile, user_numbers(self.state) | memory_numbers(self.deps, category))
        if errors:
            return drafted + "not recorded: " + "; ".join(errors) + ("\n" + profile_text(profile) if drafted else "")
        new_category = category != self.needs.category
        self.needs = needs
        self.update["needs"] = needs.model_dump(mode="json")
        text = drafted + f"recorded: {needs.text() or 'no conditions'} (category {category}, kind {needs.kind})."
        return text + ("\n" + profile_text(profile) if new_category else "")

    def search_shop(self, call: SearchShop) -> str:
        profile = self.profile()
        if profile is None:
            return "record the category with update_needs first."
        left = MAX_PRODUCT_PAGES - self.used.get("product_pages", 0)
        if left <= 0:
            return "the product pages of this turn are used up."
        self.deps.say(t("say_search_shop", self.deps.lang, words=call.words, page=call.page))
        report, pages = self.deps.search_shop(profile, self.needs, call.words, call.page, min(SEARCH_DETAILS, left))
        self.used["product_pages"] = self.used.get("product_pages", 0) + pages
        searches = dict(self.update["searches"] if "searches" in self.update else self.state.get("searches") or {})
        searches[call.words] = {"page": max(call.page, (searches.get(call.words) or {}).get("page", 0)),
                                "budget": self.needs.budget}
        self.update["searches"] = searches
        return report

    def search_web(self, call: SearchWeb) -> str:
        from scout.websearch import EnginesBusy, WebSearchUnavailable
        if self.deps.search_web is None:
            return "not available: no web search back end is set."
        if self.used.get("web_unavailable"):
            return f"not available in this turn: {self.used['web_unavailable']}."
        if self.used.get("web_searches", 0) >= MAX_WEB_SEARCHES:
            return "the web searches of this turn are used up."
        left = MAX_WEB_PAGES - self.used.get("web_pages", 0)
        if left <= 0:
            return "the web pages of this turn are used up."
        cards, unknown = self.cards(call.ids[:5])
        from scout.review_sources import site_query
        question = site_query(call.question, call.sources, self.needs.category)
        self.deps.say(t("say_search_web", self.deps.lang, question=call.question))
        # Counted before the call: a search that fails after the back end answered can have used a credit
        self.used["web_searches"] = self.used.get("web_searches", 0) + 1
        try:
            report, pages, credits = self.deps.search_web(question, [c.listing_id for c in cards], left, call.want)
        except EnginesBusy as exc:
            # Busy engines can answer again later; the second time in a turn, web search stops for the turn
            self.used["web_busy"] = self.used.get("web_busy", 0) + 1
            if self.used["web_busy"] < 2:
                return f"no results now: {exc}. Try other search words, or search again later."
            self.used["web_unavailable"] = str(exc)
            return f"not available: {exc}. Tell the shopper that the picks have no web tests."
        except WebSearchUnavailable as exc:
            # The floor does not ask for web searches that cannot be made now
            self.used["web_unavailable"] = str(exc)
            return f"not available: {exc}. Tell the shopper that the picks have no web tests."
        self.used["web_pages"] = self.used.get("web_pages", 0) + pages
        self.used["web_credits"] = self.used.get("web_credits", 0) + credits
        done = self.current("web_searches")
        self.update["web_searches"] = done + ([question] if question not in done else [])
        return report + "".join(f" There is no product with id {i}." for i in unknown)

    def research_products(self, call: ResearchProducts) -> str:
        if self.deps.research_web is None:
            return "not available: no web search back end is set. Use search_web."
        if self.used.get("web_unavailable"):
            return f"not available in this turn: {self.used['web_unavailable']}."
        searches = MAX_WEB_SEARCHES - self.used.get("web_searches", 0)
        pages = MAX_WEB_PAGES - self.used.get("web_pages", 0)
        if searches <= 0:
            return "the web searches of this turn are used up."
        if pages <= 0:
            return "the web pages of this turn are used up."
        items = research_items(call)
        no_words = sorted({i.id for i in call.products if not i.words.strip()} - {i.id for i in items})
        cards, unknown = self.cards([i.id for i in items])
        known = {c.listing_id for c in cards}
        # Each researcher judges at least one page, so there are not more researchers than pages left
        todo = [(i.id, i.words.strip()) for i in items if i.id in known][:min(MAX_RESEARCH, searches, pages)]
        later = [i.id for i in items if i.id in known][len(todo):]
        notes = "".join(f" There is no product with id {i}." for i in unknown) + (
            f" Not researched (no search words): ids {', '.join(map(str, no_words))}." if no_words else "")
        if not todo:
            return "give the ids of stored products with their search words." + notes
        from scout.websearch import RESULTS
        self.deps.say(t("say_research", self.deps.lang, n=len(todo)))
        # Counted before the call: a search that fails after the back end answered can have used a credit
        self.used["web_searches"] = self.used.get("web_searches", 0) + len(todo)
        report = self.deps.research_web(todo, min(RESULTS, max(1, pages // len(todo))), call.want)
        self.used["web_pages"] = self.used.get("web_pages", 0) + report.pages
        self.used["web_credits"] = self.used.get("web_credits", 0) + report.credits
        if report.unavailable:
            self.used["web_unavailable"] = report.unavailable
            return f"not available: {report.unavailable}. Tell the shopper that the picks have no web tests."
        done = self.current("web_searches")
        self.update["web_searches"] = done + [w for _, w in todo if w not in done]
        return report.text + notes + (
            f" Not researched (the limits of this turn): ids {', '.join(map(str, later))}." if later else "")

    def research_candidates(self, call: ResearchCandidates) -> str:
        from scout.agent.researcher import file_text, research_all
        cards, unknown = self.cards(list(dict.fromkeys(call.ids))[:MAX_RESEARCH])
        notes = "".join(f" There is no product with id {i}." for i in unknown)
        if not cards:
            return "give the ids of stored products." + notes
        ids = [c.listing_id for c in cards]
        lines = []
        # The reviews first: the browser works in one thread only, so the researchers do not read them
        if self.deps.read_reviews is not None:
            read = self.deps.reviews_read(ids)
            todo = [i for i in ids if i not in read]
            if todo:
                try:
                    lines.append("Reviews: " + self.read_reviews(ReadReviews(ids=todo)))
                except Exception as exc:  # the researchers still work with the stored text
                    lines.append(f"Reviews: not read ({type(exc).__name__}: {exc}). Call read_reviews later.")
        self.deps.say(t("say_candidates", self.deps.lang, n=len(cards)))
        found = research_all(self.deps, {**self.state, **self.update}, self.thread, cards, call.want, self.used)
        for r in found:
            for key in ("web_searches", "web_pages", "web_credits", "product_pages"):
                self.used[key] = self.used.get(key, 0) + r.used.get(key, 0)
            if r.used.get("web_unavailable") and not self.used.get("web_unavailable"):  # the floor does not ask
                self.used["web_unavailable"] = r.used["web_unavailable"]
            self.used["researcher_steps"] = self.used.get("researcher_steps", 0) + r.used.get("steps", 0)
            self.used["researchers"] = [*self.used.get("researchers", []), {
                "id": r.listing_id, "messages": r.messages, "kept": len(r.points), "dropped": r.dropped,
                "error": r.error}]
            done = self.current("web_searches")
            self.update["web_searches"] = done + [w for w in r.update.get("web_searches", []) if w not in done]
            if r.update.get("pages_read"):  # the shop pages that it read count as read in this conversation
                new = {q["id"] for q in r.update["pages_read"]}
                read = [p for p in self.current("pages_read") if p["id"] not in new]
                self.update["pages_read"] = read + [p for p in r.update["pages_read"] if p["id"] in new]
        lines += self.search_the_rest(cards)
        self.shown(ids)
        head = (f"Research of {len(cards)} product(s), one researcher each at the same time. Python found the quote "
                "of each point in the tool results of its researcher.")
        return "\n\n".join([head + notes, *lines, *map(file_text, found)])

    def search_the_rest(self, cards) -> list[str]:
        """A web search for tests of each product whose researcher did not make one, so that the floor of the picks
        does not stop them later. The pages are stored; the line only says that the search was made."""
        if self.deps.search_web is None or not self.needs.web_tests:
            return []
        searched, lines = self.deps.web_searched([c.listing_id for c in cards]), []
        for c in cards:
            if c.listing_id in searched or self.used.get("web_unavailable"):
                continue
            words = " ".join(w for w in (c.brand, c.model) if w) or " ".join(c.title.split()[:6])
            report = self.search_web(SearchWeb(question=f"{words} test", ids=[c.listing_id]))
            lines.append(f"Web search by Python for id {c.listing_id} (its researcher made none for tests): "
                         f"{' '.join(report.split())[:500]} Use search_text with its id for the stored pages.")
        return lines

    def read_page(self, call: ReadPage) -> str:
        profile = self.profile()
        if self.deps.read_page is None:
            return "not available."
        if profile is None:
            return "record the category with update_needs first."
        if self.used.get("product_pages", 0) >= MAX_PRODUCT_PAGES:
            return "the product pages of this turn are used up."
        self.deps.say(t("say_read_page", self.deps.lang, url=call.url))
        # The researchers run at the same time, and the browser works in one thread only
        report, pages, listing_id = (self.deps.read_page(call.url, profile) if self.browser
                                     else self.deps.read_page(call.url, profile, False))
        self.used["product_pages"] = self.used.get("product_pages", 0) + pages
        if listing_id is not None:
            done = [p for p in self.current("pages_read") if p["id"] != listing_id]
            self.update["pages_read"] = done + [{"id": listing_id, "url": call.url}]
        return report

    def same_product(self, call: SameProduct) -> str:
        if self.deps.same_product is None:
            return "not available."
        problem = self.deps.same_product(call.id, call.same_as)
        return f"not joined: {problem}." if problem else f"id {call.id} and id {call.same_as} are now one product."

    def shown(self, listing_ids) -> None:
        """Count the products that a result shows to the model (used["shown"]). The hidden-target test finds with
        it if the model saw the hidden product."""
        self.used["shown"] = sorted(set(self.used.get("shown", [])) | set(listing_ids))

    def find_products(self, call: FindProducts) -> str:
        profile = self.profile()
        if profile is None:
            return "record the category with update_needs first."
        needs = self.needs.model_copy(update={
            "kind": call.kind or self.needs.kind, "conditions": [*self.needs.conditions, *call.conditions],
            "budget": call.budget if call.budget is not None else self.needs.budget})
        found = self.deps.find(profile, needs, self.deps.eliminated(self.thread))
        wishes = list(dict.fromkeys(w.strip() for w in needs.preferences if w.strip()))[:5]
        verdicts, note = {}, ""
        if wishes and found and self.deps.judge_wishes:
            try:
                verdicts = self.deps.judge_wishes(found, wishes)
            except (LLMUnavailable, LLMOutputError) as exc:
                note = f" The preferences could not be judged now ({exc})."
        ranked = rank_by_wishes(found, wishes if verdicts else [], verdicts)[:max(1, min(call.limit, 30))]
        order = "the rating and the preferences" if verdicts else "the rating"
        self.shown(r.card.listing_id for r in ranked)
        return (f"{len(ranked)} stored products ({needs.text() or 'no conditions'}), ordered by {order}.{note} A "
                "missing value is not shown; it can be looked for with find_missing.\n"
                + ("\n".join(map(ranked_text, ranked)) or "none"))

    def read_product(self, call: ReadProduct) -> str:
        from scout.agent.lookup import product_text
        if self.deps.page_texts is None:
            return "not available."
        cards, unknown = self.cards(call.ids[:3])
        read = set(self.used.get("read", []))
        again = [c for c in cards if c.listing_id in read]
        cards = [c for c in cards if c.listing_id not in read]
        texts = self.deps.page_texts([c.listing_id for c in cards]) if cards else {}
        parts = [f"there is no product with id {i}." for i in unknown]
        parts += [f"id {c.listing_id} was read earlier in this turn; its text is in that result." for c in again]
        for c in cards:
            text = product_text(texts[c.listing_id]) if c.listing_id in texts else "no page text is stored."
            if len(text) > MAX_READ_CHARS:
                text = text[:MAX_READ_CHARS] + " [cut here; search_text finds a detail in the rest]"
            parts.append(f"id {c.listing_id}: {c.title[:200]}\n{text}")
        self.used["read"] = sorted(read | {c.listing_id for c in cards})
        return f"({call.question[:200]})\n\n" + "\n\n".join(parts)

    def search_text(self, call: SearchText) -> str:
        from scout.text.retrieve import Scope
        if self.deps.search_text is None:
            return "not available."
        scope = Scope(listing_ids=call.ids or None, kinds=list(call.kinds) or None, category=self.needs.category)
        # The search words in each language go to one search (scout.text.retrieve.MultiQueryRetriever)
        queries = [call.question, *call.more[:3]]
        hits = self.deps.search_text(queries if len(queries) > 1 else call.question, scope, 10)
        self.shown(h.listing_id for h in hits)
        lines = [f"[chunk {h.chunk_id}] id {h.listing_id}, {h.ref}:\n{h.text}" for h in hits]
        return (f"({call.question}) the {len(hits)} best parts. Reviews are a sample.\n\n"
                + ("\n\n".join(lines) or "nothing matches."))

    def similar_products(self, call: SimilarProducts) -> str:
        from scout.text.retrieve import NoText, Scope
        if self.deps.similar_products is None:
            return "not available."
        if self.needs.category is None:
            return "record the category with update_needs first."
        if call.like is None and not call.wish.strip():
            return "give a product id, a wish or both."
        like = []
        if call.like is not None:
            cards, unknown = self.cards([call.like])
            if unknown:
                return f"there is no product with id {call.like}."
            like = [cards[0].listing_id]
        try:
            found = self.deps.similar_products(like, [call.wish, *call.more[:3]], Scope(category=self.needs.category),
                                               max(1, min(call.limit, 15)), self.deps.eliminated(self.thread))
        except NoText:
            return f"id {call.like} has no stored text to compare with. Use the wish alone, or read_product."
        cards = {c.listing_id: c for c in self.deps.cards([s.listing_id for s in found])}
        found = [s for s in found if s.listing_id in cards]
        self.shown(s.listing_id for s in found)
        near = " and ".join(([f"id {call.like}"] if call.like is not None else [])
                            + ([f"'{call.wish}'"] if call.wish.strip() else []))
        lines = [f"{candidate_text(cards[s.listing_id])}\n  score {s.score:.3f}; nearest part ({s.reason.ref}): "
                 f"{_without_title(s.reason.text)[:300]}" for s in found]
        return (f"{len(found)} stored products nearest to {near}, nearest first. Near is not the same as fit.\n"
                + ("\n".join(lines) or "none"))

    def read_images(self, call: ReadImages) -> str:
        from scout.agent.lookup import IMAGE_HOSTS, MAX_IMAGES
        if self.deps.read_images is None or self.deps.page_texts is None:
            return "not available."
        urls = (self.deps.page_texts([call.id]).get(call.id) or {}).get("images") or []
        urls = [u for u in urls if u.startswith(IMAGE_HOSTS)][:MAX_IMAGES]
        if not urls:
            return f"no images are stored for id {call.id}."
        answer = self.deps.read_images(urls, call.question)
        return f"(id {call.id}, {call.question}) " + (answer.answer if answer.found else "the images do not show it.")

    def find_missing(self, call: FindMissing) -> str:
        profile = self.profile()
        if self.deps.find_missing is None or profile is None:
            return "not available." if profile else "record the category with update_needs first."
        cards, unknown = self.cards(call.ids)
        return self.deps.find_missing(cards, call.attributes, profile) + "".join(
            f" There is no product with id {i}." for i in unknown)

    def read_reviews(self, call: ReadReviews) -> str:
        if self.deps.read_reviews is None:
            return "not available."
        if self.used.get("review_pages", 0) >= MAX_REVIEW_PAGES:
            return "the review pages of this turn are used up."
        report, pages, unreadable = self.deps.read_reviews(call.ids)
        self.used["review_pages"] = self.used.get("review_pages", 0) + pages
        if unreadable:  # the floor does not ask again for reviews that cannot be read
            self.used["reviews_skipped"] = sorted(set(self.used.get("reviews_skipped", [])) | set(unreadable))
        return report or "the reviews of these products were read in the last 7 days."

    def summarize_reviews(self, call: SummarizeReviews) -> str:
        profile = self.profile()
        if self.deps.summarize is None or profile is None:
            return "not available." if profile else "record the category with update_needs first."
        return self.deps.summarize(profile, call.ids, last_language(self.state))

    def think(self, call: Think) -> str:
        return "noted."

    def remove_products(self, call: RemoveProducts) -> str:
        cards, unknown = self.cards(call.ids)
        if cards:
            self.deps.eliminate(self.thread, [c.product_id for c in cards], call.reason)
            removed = self.current("removed")
            removed += [{"product_id": c.product_id, "listing_id": c.listing_id, "title": c.title} for c in cards]
            self.update["removed"] = removed
            gone = {c.listing_id for c in cards}
            self.update["picks"] = [p for p in self.current("picks") if p["listing_id"] not in gone]
            if call.dislike and call.reason.strip() and self.deps.remember:  # for later conversations
                for c in cards:
                    self.deps.remember("dislike", f"{c.title[:80]}: {call.reason.strip()}", self.needs.category,
                                       self.thread, "removed")
        return (f"removed {', '.join(str(c.listing_id) for c in cards) or 'nothing'}."
                + "".join(f" There is no product with id {i}." for i in unknown))

    def remember(self, call: Remember) -> str:
        if self.deps.remember is None:
            return "not available."
        category = None if call.all_categories else self.needs.category
        memory_id = self.deps.remember(call.kind, call.text, category, self.thread)
        return f"kept as memory {memory_id} ({call.kind}, {category or 'all categories'})."

    def update_profile(self, call: UpdateProfile) -> str:
        profile = self.profile()
        if profile is None:
            return "record the category with update_needs first."
        if self.deps.save_draft is None:
            return "not available."
        attrs = {a.name: a for a in profile.attributes}
        used = {c.attribute for c in self.needs.conditions}
        errors = [f"{n} is not an attribute of the profile" for n in [*call.remove, *call.hard, *call.soft]
                  if n not in attrs]
        errors += [f"{n} is a condition of the needs; change the needs first" for n in call.remove if n in used]
        errors += [f"{n} is not a number, so it cannot be hard" for n in call.hard
                   if n in attrs and attrs[n].type != "number"]
        errors += attribute_errors(call.add, tuple(n for n in attrs if n not in call.remove))
        if errors:
            return "not recorded: " + "; ".join(errors)
        changed = [a.model_copy(update={"kind": "hard" if a.name in call.hard else "soft" if a.name in call.soft
                                        else a.kind})
                   for a in profile.attributes if a.name not in call.remove]
        try:  # the checks of CategoryProfile run again, so a saved profile always loads
            new = [ProfileAttribute(**a.model_dump()) for a in call.add]
            profile = CategoryProfile.model_validate({**profile.model_dump(), "attributes": changed + new,
                                                      "edited": True})
        except ValueError as exc:
            return f"not recorded: {exc}"
        self.deps.save_draft(profile)
        return "saved: " + profile_text(profile)

    def forget(self, call: Forget) -> str:
        if self.deps.forget is None:
            return "not available."
        gone = self.deps.forget(call.ids)
        missing = [i for i in call.ids if i not in gone]
        if not gone:
            return f"there is no memory {', '.join(map(str, missing))}."
        return f"deleted memory {', '.join(map(str, gone))}." + "".join(f" There is no memory {i}." for i in missing)

    def restore_products(self, call: RestoreProducts) -> str:
        removed = self.current("removed")
        back = [r for r in removed if r["listing_id"] in set(call.ids)]
        not_removed = [i for i in call.ids if i not in {r["listing_id"] for r in back}]
        if back:
            self.deps.restore(self.thread, [r["product_id"] for r in back])
            if self.deps.forget:  # the dislike of a product that comes back is not true now
                titles = tuple(f"{r['title'][:80]}:" for r in back)
                self.deps.forget([m["id"] for m in self.deps.memories()
                                  if m.get("source") == "removed" and " ".join(m["text"].split()).startswith(
                                      tuple(" ".join(t.split()) for t in titles))])
            self.update["removed"] = [r for r in removed if r not in back]
        return (f"brought back {', '.join(str(r['listing_id']) for r in back) or 'nothing'}."
                + "".join(f" Id {i} was not removed in this conversation." for i in not_removed))


def last_language(state: dict) -> str:
    from scout.agent.history import last_user_text
    return f"the language of this message from the shopper: {last_user_text(state)[:200]!r}"


# ---- the graph ----

def last_node(graph) -> str:
    """The node after which a turn ends. update_state writes as this node, so the next turn starts fresh."""
    return "gate" if "gate" in graph.nodes else "reviews"


def thread_of(config) -> str:
    return config["configurable"]["thread_id"]


def build_agent_graph(deps: LoopDeps, checkpointer=None):
    llm = deps.llm

    def start_node(state, config):
        deps.lang = turn_lang(state.get("messages") or [], state.get("lang"))
        return {"lang": deps.lang, "turn_context": None, "turn_messages": [], "used": {}, "gate_errors": [],
                "gate_rounds": 0, "pending": [], "final": None}

    def compact_node(state, config):
        try:
            return compact(state, llm)
        except (LLMUnavailable, LLMOutputError):
            return {}

    def agent_node(state, config):
        thread = thread_of(config)
        context = state.get("turn_context") or turn_prompt(state, deps, thread)  # the same in the whole turn
        history = list(state.get("turn_messages") or [])
        used_up = (state.get("used") or {}).get("calls", 0) >= MAX_TOOL_CALLS
        deps.step({"id": next(STEP_IDS), "kind": "think", "state": "run",
                   "title": t("step_plan" if history else "step_plan_first", deps.lang)})
        with deps.tracer.span("agent"):
            try:
                calls = llm.tools("chat", system_prompt(deps.researchers), context,
                                  work_tools(deps.researchers) + FINAL_TOOLS, name="Agent", text_reply=Reply,
                                  history=history + ([{"role": "user", "content": USED_UP}] if used_up else []))
            except LLMUnavailable as exc:
                return {"final": None, "pending": [], "gate_errors": [],
                        "messages": [assistant(t("llm_unavailable", deps.lang, exc=exc))]}
            except LLMOutputError as exc:
                return {"final": None, "pending": [], "gate_errors": [],
                        "messages": [assistant(t("llm_output", deps.lang, exc=exc))]}
        ids = [f"call_{len(history)}_{n}" for n in range(len(calls))]
        history.append(call_message(calls, ids))
        finals = [(c, i) for c, i in zip(calls, ids) if isinstance(c, tuple(FINAL_TOOLS))]
        work = [(c, i) for c, i in zip(calls, ids) if not isinstance(c, tuple(FINAL_TOOLS))]
        update = {"turn_context": context}
        if work and not used_up:
            # Each call needs a result in the history: a final call waits for the results of the other tools
            history += [result_message(i, "not run: other tools ran in the same step. Call it again when their "
                                          "results are in.") for _, i in finals]
            return {**update, "turn_messages": history, "pending": [{**to_state(c), "id": i} for c, i in work],
                    "final": None}
        history += [result_message(i, "not run: the tool calls of this turn are used up.") for _, i in work]
        history += [result_message(i, "not run: only one final call counts.") for _, i in finals[1:]]
        if finals:
            final, final_id = finals[0]
        else:
            final, final_id = Reply(text=t("turn_limits", deps.lang)), None
        return {**update, "turn_messages": history, "pending": [], "final": {**to_state(final), "id": final_id}}

    def tools_node(state, config):
        box = Toolbox(deps, state, thread_of(config))
        history = list(state.get("turn_messages") or [])
        for item in state.get("pending") or []:
            if box.used.get("calls", 0) >= MAX_TOOL_CALLS:
                report = f"{item['tool']}: not run, the tool calls of this turn are used up."
            else:
                box.used["calls"] = box.used.get("calls", 0) + 1
                call = from_state(item)
                kind, title = step_title(call, deps.lang)
                step = {"id": next(STEP_IDS), "kind": kind, "title": title}
                deps.step({**step, "state": "run"})
                began = time.monotonic()
                try:
                    with deps.tracer.span(item["tool"]):
                        report = box.run(call)
                except BaseException as exc:  # the step must not stay "run" on the page
                    deps.step({**step, "state": "fail", "detail": type(exc).__name__,
                               "seconds": round(time.monotonic() - began, 1)})
                    raise
                done, detail = step_detail(report)
                deps.step({**step, "state": done, "detail": detail, "seconds": round(time.monotonic() - began, 1),
                           "used": {k: box.used.get(k, 0) for k in LIMITS}, "limits": LIMITS})
                report = report[:REPORT_LIMITS.get(item["tool"], MAX_REPORT_CHARS)]
            history.append(result_message(item["id"], report))
        return {**box.update, "turn_messages": history, "used": box.used, "pending": []}

    def gate_node(state, config):
        thread = thread_of(config)
        call = from_state(state["final"])
        used = dict(state.get("used") or {})
        step = {"id": next(STEP_IDS), "kind": "check",
                "title": t("step_check_picks" if isinstance(call, ShowPicks) else "step_check_reply", deps.lang)}
        deps.step({**step, "state": "run"})
        errors = check_final(call, state, deps, thread, used)
        deps.step({**step, "state": "done", "detail": t("step_problems", deps.lang, n=len(errors)) if errors
                   else t("step_checks_pass", deps.lang)})
        # What the floors and the live check stopped is kept in used, for the evaluation
        if errors:
            used["gate_stops"] = used.get("gate_stops", []) + errors
        if errors and state.get("gate_rounds", 0) + 1 < MAX_GATE_ROUNDS:
            feedback = ("Python checked this call and found problems. Fix them and call show_picks or reply "
                        "again:\n- " + "\n- ".join(errors))
            history = list(state.get("turn_messages") or [])
            history.append(result_message(state["final"]["id"], feedback) if state["final"].get("id")
                           else {"role": "user", "content": feedback})
            return {"gate_errors": errors, "gate_rounds": state.get("gate_rounds", 0) + 1, "final": None,
                    "used": used, "turn_messages": history}
        if errors:
            used["gate_gave_up"] = 1
            return {"final": None, "gate_errors": [], "used": used,
                    "messages": [assistant(t("gate_gave_up", deps.lang))]}
        # The facts of the answer are graded in the evaluation (scout grade), not here
        if isinstance(call, Reply):
            return {"final": None, "gate_errors": [], "messages": [assistant(call.text)]}
        cards = {c.listing_id: c for c in deps.cards([p.id for p in call.picks])}
        needs = Needs.model_validate(state.get("needs") or {})
        profile = deps.load_profile(needs.category) if needs.category else None
        sizes = {i: page[1] for i, c in cards.items()
                 if profile and (page := size_page(c.attributes, needs, profile)) and page[1]}
        picks = [{"listing_id": p.id, "product_id": cards[p.id].product_id, "title": cards[p.id].title,
                  "doubtful": [], "note": None} for p in call.picks]
        seen = sorted(set(state.get("seen", [])) | {p["product_id"] for p in picks})
        return {"final": None, "gate_errors": [], "picks": picks, "seen": seen, "phase": "picks",
                "messages": [assistant(picks_text(call, cards, sizes, deps.lang))]}

    def after_agent(state):
        if state.get("pending"):
            return "tools"
        return "gate" if state.get("final") else END

    graph = StateGraph(ChatState)
    for name, fn in [("start", start_node), ("compact", compact_node), ("agent", agent_node),
                     ("tools", tools_node), ("gate", gate_node)]:
        graph.add_node(name, fn)
    graph.add_edge(START, "start")
    graph.add_edge("start", "compact")
    graph.add_edge("compact", "agent")
    graph.add_conditional_edges("agent", after_agent, ["tools", "gate", END])
    graph.add_edge("tools", "agent")
    graph.add_conditional_edges("gate", lambda s: "agent" if s.get("gate_errors") else END, ["agent", END])
    return graph.compile(checkpointer=checkpointer)
