"""Web search (phase 3 Task 9): Tavily finds pages, the worker judges each one from its short part, and the chat
model gets the parts of the relevant pages that answer the question.

The steps (Zheyun, 2026-10-07):
1. A Tavily search gives up to 20 pages, one credit. Some come with their text; most have only a short part.
2. The worker judges each page from its title and short part, in parallel: the kind, relevant or not, and which of
   the given products it is about.
3. The text of the relevant pages that came without text is fetched with Tavily Extract (one credit for each 5
   pages that it gets).
4. The text is cut into parts. A local reranker scores each part against the question without the brands and the
   models of the given products. The TOP_PARTS best parts go to the chat model, each with the part before and the
   part after, up to MAX_CHAT_PARTS.
5. The relevant pages about a given product are stored as chunks of that product. Pages that are not relevant keep
   only their title, address and short part.

Why the rank and not a score threshold (the context filter of GPT Researcher): in a test on 2026-10-07, with the
model name in the question nearly all parts of a page about the product scored above 0.9, menus too. Without it all
scores were below 0.07, but the order was good (buyer reviews that say "very loud" and "very quiet" came first). The
cross-encoder in place of the cosine of an embedding comes from the same test (scout.text.rerank).
"""
import contextvars
import hashlib
import math
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from typing import Callable, Literal
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field

from scout.llm.client import LLMOutputError, LLMUnavailable
from scout.text.chunks import Chunk, split_text

API = "https://api.tavily.com"
RESULTS = 20  # pages for each search (Zheyun, 2026-10-06); one credit for any number
STORED_CHARS = 30_000  # of one page
PART_TOKENS = 250  # the parts that the reranker scores, about 1,000 characters as in GPT Researcher
PART_OVERLAP = 25
TOP_PARTS = 15  # the best parts of one search, by the rank of the reranker
MAX_CHAT_PARTS = 40  # parts for the chat model in one search, the parts before and after included (~10,000 tokens)
READ_WORKERS = 10


class WebSearchUnavailable(RuntimeError):
    """No key, no credits left, or Tavily does not answer."""


@dataclass
class WebResult:
    url: str
    title: str
    content: str  # the short part that Tavily gives
    raw: str = ""  # the text of the page, when Tavily gave it
    published: str | None = None  # Tavily's estimate of when the page was published or last changed

    @property
    def site(self) -> str:
        return urlparse(self.url).netloc.removeprefix("www.")


def _date(value: str | None) -> str | None:
    """'Sat, 08 Mar 2025 00:00:00 GMT' -> '2025-03-08'."""
    if not value:
        return None
    from email.utils import parsedate_to_datetime
    try:
        return parsedate_to_datetime(value).date().isoformat()
    except (TypeError, ValueError):
        return value[:10]


class TavilyClient:
    def __init__(self, key: str, http: httpx.Client | None = None, timeout: float = 60.0):
        self.key, self.http = key, http or httpx.Client(timeout=timeout)

    def _post(self, path: str, body: dict) -> dict:
        try:
            response = self.http.post(f"{API}/{path}", headers={"Authorization": f"Bearer {self.key}"}, json=body)
        except httpx.HTTPError as exc:
            raise WebSearchUnavailable(f"Tavily did not answer ({type(exc).__name__})") from exc
        if response.status_code in (401, 403):
            raise WebSearchUnavailable("the Tavily key is not valid")
        if response.status_code in (429, 432, 433):
            raise WebSearchUnavailable(f"Tavily refused the request (status {response.status_code}): the credits "
                                       "or the rate limit of the plan are used up")
        if response.status_code != 200:
            raise WebSearchUnavailable(f"Tavily gave status {response.status_code}")
        return response.json()

    def search(self, query: str, max_results: int = RESULTS) -> list[WebResult]:
        data = self._post("search", {"query": query, "max_results": max_results, "search_depth": "basic",
                                     "include_raw_content": "text", "include_published_date": True})
        return [WebResult(r["url"], r.get("title") or "", r.get("content") or "",
                          (r.get("raw_content") or "")[:STORED_CHARS], _date(r.get("published_date")))
                for r in data.get("results", [])]

    def extract(self, urls: list[str]) -> dict[str, str]:
        """The text of each page that Tavily could get. One credit for each 5 pages that it gets."""
        texts: dict[str, str] = {}
        for start in range(0, len(urls), 20):  # at most 20 addresses in one request
            data = self._post("extract", {"urls": urls[start:start + 20], "format": "text",
                                          "extract_depth": "basic"})
            texts |= {r["url"]: (r.get("raw_content") or "")[:STORED_CHARS] for r in data.get("results", [])
                      if r.get("raw_content")}
        return texts


def url_key(url: str) -> str:
    """The address without the scheme, "www." and the last slash: Tavily can give back the address in another form."""
    url = re.sub(r"^https?://(www\.)?", "", url.strip(), flags=re.IGNORECASE)
    return url.rstrip("/").lower()


def extract_credits(pages: int) -> int:
    return math.ceil(pages / 5)


# ---- the worker judges one page from its short part ----

JUDGE_SYSTEM = """You look at one web search result for a shopping research assistant: its title, its address and a
short part of its text. Judge if the page is worth reading. You do not answer the question.

- kind: "test" for a test or review by a magazine, a blog, a video or a test site; "forum" for a forum or a
  discussion where users write, or a page of buyer reviews; "shop" for a shop page that sells products; "other"
  for anything else.
- relevant: true when the page will probably help to answer the search question: tests, measurements, opinions of
  users, problems, comparisons. When products are listed, the page must be about one of them or compare it. A shop
  page with only a product name and a price is not relevant.
- about: the ids of the listed products that the page is about (the same model, not only the same brand).
- reason: one short sentence."""


class PageJudgement(BaseModel):
    kind: Literal["test", "forum", "shop", "other"]
    relevant: bool
    about: list[int] = Field(default_factory=list)
    reason: str = ""


@dataclass
class ProductRef:
    """A product that a search is about: its listing id, the title, the model and the brand."""
    listing_id: int
    title: str
    model: str | None = None
    brand: str | None = None


def topic_question(question: str, products: list[ProductRef]) -> str:
    """The question without the brands and the models of the products, for the reranker: the pages are already
    judged to be about these products, so the parts must be scored by what they say. A model can be written with
    spaces or hyphens ("RK 4182 PS4"). Model numbers that the question names are taken out too."""
    from scout.text.embed import model_numbers
    names = {n for p in products for n in (p.model, p.brand) if n} | set(model_numbers(question))
    text = question
    for name in sorted(names, key=len, reverse=True):
        letters = [re.escape(c) for c in name if not c.isspace() and c != "-"]
        text = re.sub(r"(?<!\w)" + r"[\s-]?".join(letters) + r"(?!\w)", " ", text, flags=re.IGNORECASE)
    text = " ".join(text.split())
    return text if len(text) >= 3 else question


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
    lines += ["", f"Title: {result.title}", f"URL: {result.url}", f"Published: {result.published or 'not known'}",
              f"Short part: {result.content[:1000]}"]
    return "\n".join(lines)


def judge_page(llm, question: str, result: WebResult, products: list[ProductRef]) -> Finding:
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


# ---- the parts that answer the question ----

@dataclass
class Part:
    page: int  # index in the findings
    n: int  # place in the page
    text: str
    score: float = 0.0
    rank: int | None = None  # 1 for the best part; None for a part that comes only as a neighbour


def page_parts(findings: list[Finding]) -> list[Part]:
    parts = []
    for i, f in enumerate(findings):
        if f.relevant and f.result.raw:
            parts += [Part(i, n, text) for n, text in enumerate(split_text(f.result.raw, max_tokens=PART_TOKENS,
                                                                           overlap=PART_OVERLAP))]
    return parts


def choose_parts(parts: list[Part], top: int = TOP_PARTS, limit: int = MAX_CHAT_PARTS) -> list[Part]:
    """The top best parts, each with the part before and after it, until the limit. Returned in the order of the
    pages."""
    by_place = {(p.page, p.n): p for p in parts}
    chosen: dict[tuple[int, int], Part] = {}
    for rank, hit in enumerate(sorted(parts, key=lambda p: -p.score)[:top], 1):
        hit.rank = rank
        group = [by_place[k] for k in ((hit.page, hit.n - 1), (hit.page, hit.n), (hit.page, hit.n + 1))
                 if k in by_place and k not in chosen]
        if len(chosen) + len(group) > limit:
            continue  # a later hit can still fit when its neighbours are chosen already
        chosen |= {(p.page, p.n): p for p in group}
    return [chosen[k] for k in sorted(chosen)]


# ---- chunks and the report ----

def url_ref(url: str) -> str:
    return "web " + hashlib.sha1(url.encode()).hexdigest()[:10]


def web_chunks(product_title: str, finding: Finding) -> list[Chunk]:
    """The text of one relevant page, in chunks, for one product that it is about."""
    r = finding.result
    meta = {"url": r.url, "title": r.title[:300], "site": r.site, "type": finding.kind, "published": r.published}
    # The head is two lines: web_texts removes them, so the titles must not add a line
    head = (f"Product: {' '.join(product_title.split())}\n"
            f"Web {finding.kind}, {r.site}, {r.published or 'date not known'}: {' '.join(r.title[:200].split())}\n")
    return [Chunk(finding.kind, f"{url_ref(r.url)} {n}", head + part, meta)
            for n, part in enumerate(split_text(r.raw), 1)]


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
    credits: int  # Tavily credits: the search (0 for stored results) and the extraction
    parts: int = 0  # parts in the report


def page_line(finding: Finding) -> str:
    r = finding.result
    about = f", about id {', '.join(map(str, finding.about))}" if finding.about else ""
    return (f"{r.site}, {finding.kind}{about}, published {r.published or 'not known'}: {r.title[:150]} "
            f"({r.url})")


def search_web(question: str, products: list[ProductRef], client: TavilyClient, llm, store: WebStore,
               rerank: Callable[[str, list[str]], list[float]], max_pages: int = RESULTS,
               top: int = TOP_PARTS, want: str = "") -> WebReport:
    results = store.stored(question)
    credits = 0
    if results is None:
        results = client.search(question)
        credits = 1
    head = f"Web search '{question}'" + (" (stored results of the last 7 days)" if not credits else "")
    if not results:
        store.save_search(question, [p.listing_id for p in products], [])
        return WebReport(f"{head}: no pages found.", 0, credits)
    todo = results[:max_pages]
    findings, stopped = judge_pages(llm, question, todo, products)
    # The text of the relevant pages that came without it
    missing = [f.result.url for f in findings if f.relevant and not f.result.raw]
    extract_note = ""
    try:
        texts = client.extract(missing) if missing else {}
    except WebSearchUnavailable as exc:  # the search is paid: keep its results and go on without these texts
        texts, extract_note = {}, f"Tavily could not get the text of {len(missing)} relevant pages: {exc}."
    credits += extract_credits(len(texts))
    texts = {url_key(url): text for url, text in texts.items()}
    findings = [replace(f, result=replace(f.result, raw=texts[url_key(f.result.url)]))
                if url_key(f.result.url) in texts else f for f in findings]
    # Only the relevant pages keep their text
    store.save_search(question, [p.listing_id for p in products],
                      [f.result if f.relevant else replace(f.result, raw="") for f in findings]
                      + results[len(todo):])
    titles = {p.listing_id: p.title for p in products}
    for f in findings:
        if f.relevant and f.result.raw:
            for listing_id in f.about:
                store.save_chunks(listing_id, f.result.url, web_chunks(titles[listing_id], f))
    parts = page_parts(findings)
    topic = want.strip() or topic_question(question, products)
    for part, score in zip(parts, rerank(topic, [f"Page: {findings[p.page].result.title}\n{p.text}" for p in parts])):
        part.score = score
    chosen = choose_parts(parts, top)
    relevant = [i for i, f in enumerate(findings) if f.relevant]
    lines = [f"{head}: {len(results)} pages found, {len(todo)} judged by the worker, {len(relevant)} relevant. "
             f"The {sum(p.rank is not None for p in chosen)} parts of their text that answer '{topic}' best, "
             "ranked by a reranker, with the part before and after each."]
    best: dict[int, int] = {}  # page -> the rank of its best part
    for p in chosen:
        if p.rank is not None:
            best[p.page] = min(best.get(p.page, p.rank), p.rank)
    for n, page in enumerate(sorted(best, key=lambda i: best[i]), 1):
        lines += ["", f"[web page {n}] {page_line(findings[page])}"]
        last = None
        for p in (p for p in chosen if p.page == page):
            if last is not None and p.n != last + 1:
                lines.append("[...]")
            lines.append((f"(best part {p.rank}) " if p.rank is not None else "") + p.text)
            last = p.n
    others = [i for i in range(len(findings)) if i not in best]
    if others:
        lines += ["", "The other pages:"]
        for i in others:
            f = findings[i]
            why = f.error or ("relevant; no part was among the best" if f.relevant and f.result.raw else
                              "relevant; Tavily could not get its text" if f.relevant else
                              "not relevant" + (f", {f.reason}" if f.reason else ""))
            lines.append(f"- {page_line(f)}: {why}")
    if stopped:
        lines.append(stopped)
    if extract_note:
        lines.append(extract_note)
    if len(results) > len(todo):
        lines.append(f"{len(results) - len(todo)} more pages were found and not judged (the web pages of this turn "
                     "are used up).")
    return WebReport("\n".join(lines), len(todo), credits, len(chosen))
