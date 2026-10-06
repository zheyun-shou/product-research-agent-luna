"""Web search (phase 3 Task 9): Tavily finds pages, the worker reads them, Python checks the quotes.

Tavily gives the text of each found page (include_raw_content), so product-scout does not fetch these pages itself.
A basic search costs one credit; the free plan has 1,000 each month. The same question in the last 7 days uses the
stored results and costs nothing.
"""
import contextvars
import hashlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Literal
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field

from scout.extract.verify import verify_raw
from scout.llm.client import LLMOutputError, LLMUnavailable
from scout.text.chunks import Chunk

API = "https://api.tavily.com/search"
RESULTS = 20  # pages for each search (Zheyun, 2026-10-06); one credit for any number
PAGE_CHARS = 15_000  # of one page for the worker
STORED_CHARS = 30_000  # of one page in the stored results
READ_WORKERS = 4
MAX_QUOTES = 6  # for each product on one page
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


# ---- the worker reads one page ----

READ_SYSTEM = """You read one web page for a shopping research assistant. Say what kind of page it is, and copy the
parts of the page that are about each listed product.

- kind: "test" for a test or review by a magazine, a blog or a test site; "forum" for a forum or a discussion where
  users write; "shop" for a shop page that sells products; "other" for anything else.
- For each listed product that the page is about (the same model, not only the same brand), give quotes: the
  sentences of the page about that product, copied exactly, each at most 3 sentences. Opinions, test results,
  measured values and problems are the most useful. At most 6 quotes for each product.
- A page that compares many products: give only the quotes about the listed ones.
- Leave out a product that the page does not name. Never write a quote that is not in the page."""


class ProductQuotes(BaseModel):
    id: int = Field(description="The product id from the list")
    quotes: list[str] = Field(default_factory=list)


class PageRead(BaseModel):
    kind: Literal["test", "forum", "shop", "other"]
    products: list[ProductQuotes] = Field(default_factory=list)


@dataclass
class ProductRef:
    """A product that a search is about: its listing id, the title and the model."""
    listing_id: int
    title: str
    model: str | None = None


@dataclass
class Finding:
    result: WebResult
    kind: str
    quotes: dict[int, list[str]] = field(default_factory=dict)  # listing id -> quotes that are in the page
    dropped: int = 0  # quotes that are not in the page
    error: str = ""


def read_prompt(result: WebResult, products: list[ProductRef]) -> str:
    lines = ["Products:"] + [f"- id {p.listing_id}: {p.title[:200]}" + (f" (model {p.model})" if p.model else "")
                             for p in products]
    lines += ["", f"Page: {result.title}", f"URL: {result.url}", "", (result.raw or result.content)[:PAGE_CHARS]]
    return "\n".join(lines)


def read_page_text(llm, result: WebResult, products: list[ProductRef]) -> Finding:
    """One worker call. Python keeps a quote only when it is in the page text."""
    text = result.raw or result.content
    try:
        read = llm.structured("worker", READ_SYSTEM, read_prompt(result, products), PageRead)
    except LLMOutputError as exc:
        return Finding(result, "other", error=f"the worker gave no usable reply ({exc})")
    known = {p.listing_id for p in products}
    finding = Finding(result, read.kind)
    for item in read.products:
        if item.id not in known:
            continue
        for quote in item.quotes[:MAX_QUOTES]:
            if verify_raw(quote, text):
                finding.quotes.setdefault(item.id, []).append(quote.strip())
            else:
                finding.dropped += 1
    return finding


def read_pages(llm, results: list[WebResult], products: list[ProductRef]) -> tuple[list[Finding], str]:
    """Read the pages in parallel. Returns the findings and a note when the worker stopped."""
    findings: list[Finding] = []
    stopped = ""
    with ThreadPoolExecutor(READ_WORKERS) as pool:
        futures = [(r, pool.submit(contextvars.copy_context().run, read_page_text, llm, r, products))
                   for r in results]
        for result, future in futures:
            try:
                findings.append(future.result())
            except LLMUnavailable as exc:
                stopped = f"The worker model is not available ({exc}), so some pages were not read."
                findings.append(Finding(result, "other", error="not read"))
    return findings, stopped


# ---- chunks and the report ----

def url_ref(url: str) -> str:
    return "web " + hashlib.sha1(url.encode()).hexdigest()[:10]


def web_chunks(product_title: str, finding: Finding, listing_id: int) -> list[Chunk]:
    """The quotes of one page about one product, as one chunk for each quote."""
    r = finding.result
    meta = {"url": r.url, "title": r.title[:300], "site": r.site, "type": finding.kind}
    head = f"Product: {product_title}\nWeb {finding.kind}, {r.site}: {r.title[:200]}\n"
    return [Chunk(finding.kind, f"{url_ref(r.url)} {n}", head + quote, meta)
            for n, quote in enumerate(finding.quotes.get(listing_id, []), 1)]


@dataclass
class WebStore:
    """The database parts of a web search. Tests give fakes."""
    stored: Callable[[str], list[WebResult] | None]  # the results of this question from the last 7 days
    save_search: Callable[[str, list[int], list[WebResult]], None]  # (question, listing ids, results)
    save_chunks: Callable[[int, str, list[Chunk]], None]  # (listing id, url, chunks of that page)


@dataclass
class WebReport:
    text: str
    pages: int  # pages that the worker read
    credits: int  # 0 when the stored results were used


def search_web(question: str, products: list[ProductRef], client: TavilyClient, llm, store: WebStore,
               max_pages: int = RESULTS) -> WebReport:
    """Search, then read the found pages for the given products and store the checked quotes as chunks. Without
    products, give only the titles, the addresses and the short parts that Tavily found."""
    results = store.stored(question)
    credits = 0
    if results is None:
        results = client.search(question)
        credits = 1
    store.save_search(question, [p.listing_id for p in products], results)
    head = f"Web search '{question}'" + (" (stored results of the last 7 days)" if not credits else "")
    if not results:
        return WebReport(f"{head}: no pages found.", 0, credits)
    if not products:
        lines = [f"- {r.title[:150]} | {r.site} | {r.url}\n  {r.content[:400]}" for r in results]
        return WebReport(f"{head}: {len(results)} pages. Give product ids to read the pages for those products.\n"
                         + "\n".join(lines), 0, credits)
    todo = results[:max_pages]
    findings, stopped = read_pages(llm, todo, products)
    titles = {p.listing_id: p.title for p in products}
    lines = [f"{head}: read {len(todo)} pages."]
    for finding in findings:
        r = finding.result
        line = f"- {r.site}, {finding.kind}: {r.title[:150]} ({r.url})"
        if finding.error:
            lines.append(f"{line}: {finding.error}.")
            continue
        for listing_id in titles:
            if finding.kind in WEB_KINDS:
                store.save_chunks(listing_id, r.url, web_chunks(titles[listing_id], finding, listing_id))
        if finding.kind == "shop":
            lines.append(f"{line}: a shop page; its offers are not read yet.")
        elif not finding.quotes:
            lines.append(f"{line}: nothing about these products.")
        else:
            lines.append(line + "".join(f"\n  id {i}: " + " | ".join(q) for i, q in finding.quotes.items()))
        if finding.dropped:
            lines.append(f"  {finding.dropped} quotes of the worker were not in the page and were dropped.")
    if stopped:
        lines.append(stopped)
    if len(results) > len(todo):
        lines.append(f"{len(results) - len(todo)} more pages were found and not read (page limit of this turn).")
    return WebReport("\n".join(lines), len(todo), credits)
