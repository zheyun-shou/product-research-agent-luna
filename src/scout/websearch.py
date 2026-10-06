"""Web search (phase 3 Task 9): Tavily finds pages with their text, the worker judges each page, and the chat model
reads the full text of the relevant pages.

Tavily gives the text of each found page (include_raw_content), so product-scout does not fetch these pages itself.
A basic search costs one credit for any number of results; the free plan has 1,000 each month. The same question in
the last 7 days uses the stored results and costs nothing.

Decided by Zheyun on 2026-10-07: the worker reads all found pages in parallel and only judges them. The chat model
gets the page text itself, not a summary, and at least MIN_TO_CHAT pages.
"""
import contextvars
import hashlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Literal
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field

from scout.llm.client import LLMOutputError, LLMUnavailable
from scout.text.chunks import Chunk, split_text

API = "https://api.tavily.com/search"
RESULTS = 20  # pages for each search (Zheyun, 2026-10-06); one credit for any number
STORED_CHARS = 30_000  # of one page in the stored results
PAGE_CHARS = 15_000  # of one page for the worker and for the chat model
READ_WORKERS = 10
MIN_TO_CHAT = 5  # pages whose text the chat model gets, also when fewer are relevant
MAX_TO_CHAT = 8
WEB_KINDS = ("test", "forum")


class WebSearchUnavailable(RuntimeError):
    """No key, no credits left, or Tavily does not answer."""


@dataclass
class WebResult:
    url: str
    title: str
    content: str  # the short part that Tavily gives
    raw: str = ""  # the text of the page

    @property
    def site(self) -> str:
        return urlparse(self.url).netloc.removeprefix("www.")

    @property
    def text(self) -> str:
        return self.raw or self.content


class TavilyClient:
    def __init__(self, key: str, http: httpx.Client | None = None, timeout: float = 60.0):
        self.key, self.http, self.timeout = key, http or httpx.Client(timeout=timeout), timeout

    def search(self, query: str, max_results: int = RESULTS) -> list[WebResult]:
        try:
            response = self.http.post(API, headers={"Authorization": f"Bearer {self.key}"}, json={
                "query": query, "max_results": max_results, "search_depth": "basic",
                "include_raw_content": "text"})
        except httpx.HTTPError as exc:
            raise WebSearchUnavailable(f"Tavily did not answer ({type(exc).__name__})") from exc
        if response.status_code in (401, 403):
            raise WebSearchUnavailable("the Tavily key is not valid")
        if response.status_code in (429, 432, 433):
            raise WebSearchUnavailable(f"Tavily refused the search (status {response.status_code}): the credits "
                                       "or the rate limit of the plan are used up")
        if response.status_code != 200:
            raise WebSearchUnavailable(f"Tavily gave status {response.status_code}")
        return [WebResult(r["url"], r.get("title") or "", r.get("content") or "", (r.get("raw_content") or "")[
            :STORED_CHARS]) for r in response.json().get("results", [])]


# ---- the worker judges one page ----

JUDGE_SYSTEM = """You look at one web page for a shopping research assistant. The assistant will read the relevant
pages itself. You only judge the page.

- kind: "test" for a test or review by a magazine, a blog or a test site; "forum" for a forum or a discussion where
  users write; "shop" for a shop page that sells products; "other" for anything else.
- relevant: true when the page helps to answer the search question: tests, measurements, opinions of users,
  problems, comparisons. When products are listed, the page must be about one of them or compare it. A page with
  only a product name and a price, or no real text, is not relevant.
- about: the ids of the listed products that the page is about (the same model, not only the same brand).
- reason: one short sentence, why it is relevant or not."""


class PageJudgement(BaseModel):
    kind: Literal["test", "forum", "shop", "other"]
    relevant: bool
    about: list[int] = Field(default_factory=list)
    reason: str = ""


@dataclass
class ProductRef:
    """A product that a search is about: its listing id, the title and the model."""
    listing_id: int
    title: str
    model: str | None = None


@dataclass
class Finding:
    result: WebResult
    kind: str = "other"
    relevant: bool = False
    about: list[int] = field(default_factory=list)
    reason: str = ""
    error: str = ""


def judge_prompt(question: str, result: WebResult, products: list[ProductRef]) -> str:
    lines = [f"Search question: {question}", "Products:"]
    lines += [f"- id {p.listing_id}: {p.title[:200]}" + (f" (model {p.model})" if p.model else "")
              for p in products] or ["none listed"]
    lines += ["", f"Page: {result.title}", f"URL: {result.url}", "", result.text[:PAGE_CHARS]]
    return "\n".join(lines)


def judge_page(llm, question: str, result: WebResult, products: list[ProductRef]) -> Finding:
    if not result.text.strip():
        return Finding(result, reason="the page has no text")
    try:
        judged = llm.structured("worker", JUDGE_SYSTEM, judge_prompt(question, result, products), PageJudgement)
    except LLMOutputError as exc:
        return Finding(result, error=f"the worker gave no usable reply ({exc})")
    known = {p.listing_id for p in products}
    return Finding(result, judged.kind, judged.relevant, [i for i in judged.about if i in known],
                   judged.reason[:200])


def judge_pages(llm, question: str, results: list[WebResult], products: list[ProductRef]
                ) -> tuple[list[Finding], str]:
    """Judge all pages in parallel. Returns the findings in the order of the results, and a note when the worker
    stopped."""
    findings: list[Finding] = []
    stopped = ""
    with ThreadPoolExecutor(READ_WORKERS) as pool:
        futures = [(r, pool.submit(contextvars.copy_context().run, judge_page, llm, question, r, products))
                   for r in results]
        for result, future in futures:
            try:
                findings.append(future.result())
            except LLMUnavailable as exc:
                stopped = f"The worker model is not available ({exc}), so some pages were not judged."
                findings.append(Finding(result, error="not judged"))
    return findings, stopped


def for_chat(findings: list[Finding]) -> list[Finding]:
    """The relevant pages in the order of the search, at most MAX_TO_CHAT. When fewer than MIN_TO_CHAT are
    relevant, the next pages with text fill up to MIN_TO_CHAT."""
    chosen = [f for f in findings if f.relevant][:MAX_TO_CHAT]
    rest = [f for f in findings if not f.relevant and f.result.text.strip()]
    return chosen + rest[:max(0, MIN_TO_CHAT - len(chosen))]


# ---- chunks and the report ----

def url_ref(url: str) -> str:
    return "web " + hashlib.sha1(url.encode()).hexdigest()[:10]


def web_chunks(product_title: str, finding: Finding) -> list[Chunk]:
    """The text of one test or forum page, in chunks, for one product that it is about."""
    r = finding.result
    meta = {"url": r.url, "title": r.title[:300], "site": r.site, "type": finding.kind}
    head = f"Product: {product_title}\nWeb {finding.kind}, {r.site}: {r.title[:200]}\n"
    return [Chunk(finding.kind, f"{url_ref(r.url)} {n}", head + part, meta)
            for n, part in enumerate(split_text(r.text[:STORED_CHARS]), 1)]


@dataclass
class WebStore:
    """The database parts of a web search. Tests give fakes."""
    stored: Callable[[str], list[WebResult] | None]  # the results of this question from the last 7 days
    save_search: Callable[[str, list[int], list[WebResult]], None]  # (question, listing ids, results)
    save_chunks: Callable[[int, str, list[Chunk]], None]  # (listing id, url, chunks of that page)


@dataclass
class WebReport:
    text: str
    pages: int  # pages that the worker judged
    credits: int  # 0 when the stored results were used
    to_chat: int = 0  # pages whose text is in the report


def page_line(finding: Finding) -> str:
    r = finding.result
    about = f", about id {', '.join(map(str, finding.about))}" if finding.about else ""
    return f"{r.site}, {finding.kind}{about}: {r.title[:150]} ({r.url})"


def search_web(question: str, products: list[ProductRef], client: TavilyClient, llm, store: WebStore,
               max_pages: int = RESULTS) -> WebReport:
    """Search, let the worker judge each found page, and give the text of the relevant pages. Test and forum pages
    about a listed product are stored as chunks of that product."""
    results = store.stored(question)
    credits = 0
    if results is None:
        results = client.search(question)
        credits = 1
    store.save_search(question, [p.listing_id for p in products], results)
    head = f"Web search '{question}'" + (" (stored results of the last 7 days)" if not credits else "")
    if not results:
        return WebReport(f"{head}: no pages found.", 0, credits)
    todo = results[:max_pages]
    findings, stopped = judge_pages(llm, question, todo, products)
    titles = {p.listing_id: p.title for p in products}
    for finding in findings:
        if finding.relevant and finding.kind in WEB_KINDS:
            for listing_id in finding.about:
                store.save_chunks(listing_id, finding.result.url, web_chunks(titles[listing_id], finding))
    chosen = for_chat(findings)
    relevant = sum(f.relevant for f in findings)
    lines = [f"{head}: {len(results)} pages found, {len(todo)} judged by the worker, {relevant} relevant. "
             f"The text of {len(chosen)} pages follows."]
    for n, finding in enumerate(chosen, 1):
        mark = "" if finding.relevant else " (the worker found it not relevant)"
        text = finding.result.text
        cut = " [cut here]" if len(text) > PAGE_CHARS else ""
        lines += ["", f"[web page {n}] {page_line(finding)}{mark}", text[:PAGE_CHARS] + cut]
    others = [f for f in findings if f not in chosen]
    if others:
        lines += ["", "The other pages:"]
        lines += [f"- {page_line(f)}: " + (f.error or ("relevant" if f.relevant else "not relevant")
                                            + (f", {f.reason}" if f.reason else "")) for f in others]
    if stopped:
        lines.append(stopped)
    if len(results) > len(todo):
        lines.append(f"{len(results) - len(todo)} more pages were found and not judged (the web pages of this turn "
                     "are used up).")
    return WebReport("\n".join(lines), len(todo), credits, len(chosen))
