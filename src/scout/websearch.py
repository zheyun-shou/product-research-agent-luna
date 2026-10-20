"""Web search (phase 3 Task 9): Tavily finds pages, the worker judges each one from its short part, and the chat
model gets the parts of the relevant pages that answer the question.

The steps (my choice, 2026-10-07):
1. A Tavily search gives up to 20 pages, one credit. Some come with their text; most have only a short part.
2. The worker judges each page from its title and short part, in parallel: the kind, relevant or not, and which of
   the given products it is about.
3. Our own fetcher (scout.fetch) gets the text of the relevant pages that came without text (since 2026-10-07;
   before, Tavily Extract got it for one credit for each 5 pages).
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
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Callable, Literal
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field

from scout.llm.client import LLMOutputError, LLMUnavailable
from scout.text.chunks import Chunk, split_text

if TYPE_CHECKING:
    from scout.fetch import Fetched

API = "https://api.tavily.com"
RESULTS = 20  # pages for each search (my choice, 2026-10-06); one credit for any number
STORED_CHARS = 30_000  # of one page
PART_TOKENS = 250  # the parts that the reranker scores, about 1,000 characters as in GPT Researcher
PART_OVERLAP = 25
TOP_PARTS = 15  # the best parts of one search, by the rank of the reranker
MAX_CHAT_PARTS = 40  # parts for the chat model in one search, the parts before and after included (~10,000 tokens)
READ_WORKERS = 10


class WebSearchUnavailable(RuntimeError):
    """No search back end answers: no key, no credits left, or the service does not run."""


class EnginesBusy(WebSearchUnavailable):
    """SearXNG found nothing because its engines did not answer now (a captcha, too many requests). It can work
    again later in the same turn."""


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
    name = "Tavily"
    search_credits = 1

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


class SearxngClient:
    """Our own SearXNG in Docker (decided 2026-10-07): it asks Google, Bing, DuckDuckGo and other engines and gives
    their results as JSON. No key and no credits. An engine that shows a captcha is left out by SearXNG; the
    program never bypasses it. SearXNG gives no page texts: our own fetcher reads them."""
    search_credits = 0
    name = "SearXNG"

    def __init__(self, url: str = "http://127.0.0.1:8888", http: httpx.Client | None = None, timeout: float = 15.0):
        self.url, self.http = url.rstrip("/"), http or httpx.Client(timeout=timeout)

    def _page(self, query: str, pageno: int) -> dict:
        try:
            response = self.http.get(f"{self.url}/search", params={"q": query, "format": "json", "pageno": pageno})
        except httpx.HTTPError as exc:
            raise WebSearchUnavailable(f"SearXNG does not answer ({type(exc).__name__}); start it with "
                                       "docker compose up -d searxng") from exc
        if response.status_code != 200:
            raise WebSearchUnavailable(f"SearXNG gave status {response.status_code}")
        try:
            data = response.json()
        except ValueError as exc:  # an error page in place of the JSON
            raise WebSearchUnavailable("SearXNG did not give JSON") from exc
        return data if isinstance(data, dict) else {}

    def search(self, query: str, max_results: int = RESULTS) -> list[WebResult]:
        """The results of the first result pages until max_results (one page has about 10 to 20)."""
        found: list[WebResult] = []
        busy: list = []
        for pageno in (1, 2):
            data = self._page(query, pageno)
            busy = data.get("unresponsive_engines") or busy
            page = [WebResult(r["url"], r.get("title") or "", r.get("content") or "", "",
                              (r.get("publishedDate") or "")[:10] or None)
                    for r in data.get("results", []) if isinstance(r, dict) and r.get("url")]
            found += page
            if not page or len(unique_results(found)) >= max_results:
                break
        found = unique_results(found)
        if not found and busy:
            engines = ", ".join(f"{name} ({why})" for name, why in busy)
            raise EnginesBusy(f"the search engines did not answer now: {engines}")
        return found[:max_results]

    def extract(self, urls: list[str]) -> dict[str, str]:
        raise WebSearchUnavailable("SearXNG gives no page texts")


class FirstThatAnswers:
    """The search back ends in order: the next one searches when one is not available. The note says why."""

    def __init__(self, clients: list):
        self.clients = clients

    def search_info(self, query: str, max_results: int = RESULTS) -> tuple[list[WebResult], int, str]:
        """(results, credits, note)."""
        problems, errors = [], []
        for client in self.clients:
            try:
                results = client.search(query, max_results)
            except WebSearchUnavailable as exc:
                problems.append(f"{getattr(client, 'name', 'a back end')}: {exc}")
                errors.append(exc)
                continue
            credits = getattr(client, "search_credits", 1)
            note = (f"{'; '.join(problems)}. So {getattr(client, 'name', 'the next back end')} searched"
                    + (f" ({credits} credit)." if credits else ".")) if problems else ""
            return results, credits, note
        busy = bool(errors) and all(isinstance(e, EnginesBusy) for e in errors)
        raise (EnginesBusy if busy else WebSearchUnavailable)("; ".join(problems) or "no search back end")

    def search(self, query: str, max_results: int = RESULTS) -> list[WebResult]:
        return self.search_info(query, max_results)[0]

    def extract(self, urls: list[str]) -> dict[str, str]:
        for client in self.clients:
            try:
                return client.extract(urls)
            except WebSearchUnavailable:
                continue
        raise WebSearchUnavailable("no back end gives page texts")


def search_once(client, query: str) -> tuple[list[WebResult], int, str]:
    """(results, credits, note) of one search with any back end."""
    if hasattr(client, "search_info"):
        return client.search_info(query)
    return client.search(query), getattr(client, "search_credits", 1), ""


def unique_results(results: list[WebResult]) -> list[WebResult]:
    """One result for each address: back ends can give the same page with and without "www." or the last slash."""
    seen, kept = set(), []
    for r in results:
        key = url_key(r.url)
        if key and key not in seen:
            seen.add(key)
            kept.append(r)
    return kept


def url_key(url: str) -> str:
    """The address without the scheme, "www." and the last slash: Tavily can give back the address in another form."""
    url = re.sub(r"^https?://(www\.)?", "", url.strip(), flags=re.IGNORECASE)
    return url.rstrip("/").lower()


def extract_credits(pages: int) -> int:
    return math.ceil(pages / 5)


THREAD_PAGES = 2  # more pages of one forum thread


def thread_pages(html: str, url: str, limit: int = THREAD_PAGES) -> list[str]:
    """The next pages of a forum thread that the page links to: the same address with ?p=N or ?page=N, N from 2.
    Only on the forums of config/review_sources.yaml (on Prisjakt, ?p= is a product), and only from the first page."""
    from urllib.parse import urljoin, urlsplit
    from scout.review_sources import is_forum
    base = urlsplit(url)
    if not is_forum(url) or base.query:
        return []
    found: dict[int, str] = {}
    for href in re.findall(r'href="([^"]+)"', html):
        link = urlsplit(urljoin(url, href.replace("&amp;", "&")))
        number = re.fullmatch(r"(?:p|page)=(\d+)", link.query)
        if number and link.netloc == base.netloc and link.path == base.path and int(number.group(1)) >= 2:
            found.setdefault(int(number.group(1)), link.geturl())
    return [found[n] for n in sorted(found)][:limit]


def fetch_texts(fetch: Callable[[str], "Fetched"], urls: list[str], workers: int = READ_WORKERS) -> dict[str, str]:
    """The main text of each page that our own fetcher could read (scout.fetch), in place of Tavily Extract
    (decided 2026-10-07). Pages of different sites load in parallel; the fetcher keeps one page of a site at a
    time."""
    from scout.sources.generic import page_text

    def one(url: str) -> str:
        got = fetch(url)
        if got.page is None:
            return ""
        text = page_text(got.page.html, got.page.final_url)
        # A forum thread: its next pages, one at a time (the fetcher keeps the gap of the site)
        for later in thread_pages(got.page.html, got.page.final_url):
            if len(text) >= STORED_CHARS:
                break
            more = fetch(later)
            if more.page is None:
                break
            text += f"\n\n[page {later}]\n" + page_text(more.page.html, more.page.final_url)
            if len(text) >= STORED_CHARS:
                break
        return text[:STORED_CHARS]
    with ThreadPoolExecutor(workers) as pool:
        texts = dict(zip(urls, pool.map(one, urls)))
    return {url: text for url, text in texts.items() if text}


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
    # The sites of a search of review sources ("(site:a OR site:b)") are not a topic
    question = re.sub(r"\s*\((?:site:\S+(?:\s+OR\s+)?)+\)\s*$", "", question) or question
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
    # The chosen parts of each page, best page first: the page facts (site, kind, published, title, url) and "text"
    sources: list[dict] = field(default_factory=list)


def page_line(finding: Finding) -> str:
    r = finding.result
    about = f", about id {', '.join(map(str, finding.about))}" if finding.about else ""
    return (f"{r.site}, {finding.kind}{about}, published {r.published or 'not known'}: {r.title[:150]} "
            f"({r.url})")


def search_web(question: str, products: list[ProductRef], client: TavilyClient, llm, store: WebStore,
               rerank: Callable[[str, list[str]], list[float]], max_pages: int = RESULTS,
               top: int = TOP_PARTS, want: str = "",
               extract: Callable[[list[str]], dict[str, str]] | None = None) -> WebReport:
    """`extract(urls)` gets the text of the relevant pages that came without it: our own fetcher, free. Without it
    Tavily Extract gets them, for credits."""
    results = store.stored(question)
    credits, stored, back_end_note = 0, results is not None, ""
    if results is None:
        results, credits, back_end_note = search_once(client, question)  # Tavily 1 credit, SearXNG 0
    results = unique_results(results)
    head = f"Web search '{question}'" + (" (stored results of the last 7 days)" if stored else "")
    if not results:
        store.save_search(question, [p.listing_id for p in products], [])
        return WebReport(f"{head}: no pages found." + (f" {back_end_note}" if back_end_note else ""), 0, credits)
    todo = results[:max_pages]
    findings, stopped = judge_pages(llm, question, todo, products)
    # The text of the relevant pages that came without it
    missing = [f.result.url for f in findings if f.relevant and not f.result.raw]
    extract_note = ""
    try:
        texts = (extract or client.extract)(missing) if missing else {}
    except WebSearchUnavailable as exc:  # the search is paid: keep its results and go on without these texts
        texts, extract_note = {}, f"The text of {len(missing)} relevant pages could not be read: {exc}."
    if extract is None:
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
    sources = []
    for n, page in enumerate(sorted(best, key=lambda i: best[i]), 1):
        lines += ["", f"[web page {n}] {page_line(findings[page])}"]
        last, texts = None, []
        for p in (p for p in chosen if p.page == page):
            if last is not None and p.n != last + 1:
                lines.append("[...]")
                texts.append("[...]")
            lines.append((f"(best part {p.rank}) " if p.rank is not None else "") + p.text)
            texts.append(p.text)
            last = p.n
        r = findings[page].result
        sources.append({"site": r.site, "kind": findings[page].kind, "published": r.published, "title": r.title,
                        "url": r.url, "text": "\n".join(texts)})
    others = [i for i in range(len(findings)) if i not in best]
    if others:
        lines += ["", "The other pages:"]
        for i in others:
            f = findings[i]
            why = f.error or ("relevant; no part was among the best" if f.relevant and f.result.raw else
                              "relevant; its text could not be read" if f.relevant else
                              "not relevant" + (f", {f.reason}" if f.reason else ""))
            lines.append(f"- {page_line(f)}: {why}")
    if stopped:
        lines.append(stopped)
    if extract_note:
        lines.append(extract_note)
    if back_end_note:
        lines.append(back_end_note)
    if len(results) > len(todo):
        lines.append(f"{len(results) - len(todo)} more pages were found and not judged (the web pages of this turn "
                     "are used up).")
    return WebReport("\n".join(lines), len(todo), credits, len(chosen), sources)


# ---- researchers: one web search for each product at the same time, summed up by the worker ----
# The "conduct research" step of open_deep_research (phase 3, later with Task 9; built on 2026-10-20). The chat model
# gets short points with quotes in place of up to 40 parts of each search, so five products fit in its context.

RESEARCHERS = 3  # searches at the same time; SearXNG asks other engines, so not too many
BRIEF_POINTS = 6
QUOTE_WORDS = 30

BRIEF_SYSTEM = f"""You read the best parts of web pages about one product, for a shopping research assistant. Write
what the pages say that helps a shopper decide: tests, measurements, problems, what owners like and dislike.

- points: at most {BRIEF_POINTS}, the most useful first. Each point has a short sentence in English (text), the number
  of the page, and a quote: the exact words of that page that show the point, at most {QUOTE_WORDS} words, in the
  language of the page. Copy the quote; do not translate or change it.
- Only what the pages say about this product. A page about another model or variant (another size, colour or
  version) does not count; leave it out, or say in the point that it is about another variant.
- missing: in one short sentence, what the pages do not say about the wish of the shopper. Empty when nothing is
  missing."""


class BriefPoint(BaseModel):
    text: str
    page: int
    quote: str


class Brief(BaseModel):
    points: list[BriefPoint] = Field(default_factory=list)
    missing: str = ""


def _plain(text: str) -> str:
    return " ".join(re.sub(r"[\"'`“”„«»‘’]", "", text).split()).lower()


def quote_found(quote: str, text: str) -> bool:
    """The quote is in the text, apart from spaces, case and quote marks. A quote with "..." needs each piece."""
    pieces = [_plain(piece).strip(" .,;:") for piece in re.split(r"\.\.\.|…|\[\.\.\.\]", quote)]
    pieces = [piece for piece in pieces if piece]
    page = _plain(text)
    return bool(pieces) and sum(map(len, pieces)) >= 8 and all(piece in page for piece in pieces)


def brief_prompt(product: ProductRef, want: str, sources: list[dict]) -> str:
    lines = [f"Product: {product.title[:200]}" + (f" (model {product.model})" if product.model else ""),
             f"Wish of the shopper: {want.strip() or 'tests, problems and opinions of owners'}"]
    for n, source in enumerate(sources, 1):
        lines += ["", f"[page {n}] {source['site']}, {source['kind']}, published {source['published'] or 'not known'}"
                      f": {source['title'][:150]}", source["text"]]
    return "\n".join(lines)


@dataclass
class Research:
    product: ProductRef
    words: str
    report: WebReport | None = None
    points: list[tuple[BriefPoint, dict]] = field(default_factory=list)  # each point with its page
    missing: str = ""
    dropped: int = 0  # points whose quote Python did not find in the page
    error: str = ""
    unavailable: bool = False  # the search back end was not available (not only busy)


def research_one(product: ProductRef, words: str, client, llm, store: WebStore,
                 rerank: Callable[[str, list[str]], list[float]], max_pages: int, want: str = "",
                 extract: Callable[[list[str]], dict[str, str]] | None = None) -> Research:
    """One researcher: one web search for one product, and the worker's points from the best parts."""
    done = Research(product, words)
    try:
        done.report = search_web(words, [product], client, llm, store, rerank, max_pages, want=want, extract=extract)
    except WebSearchUnavailable as exc:
        done.error, done.unavailable = str(exc), not isinstance(exc, EnginesBusy)
        return done
    except Exception as exc:  # one researcher must not lose the results of the others
        done.error = f"the search failed ({type(exc).__name__}: {exc})"
        return done
    if not done.report.sources:
        return done
    try:
        brief = llm.structured("worker", BRIEF_SYSTEM, brief_prompt(product, want, done.report.sources), Brief)
    except Exception as exc:  # LLMOutputError, LLMUnavailable or a problem of the provider
        done.error = f"the worker could not sum up the pages ({type(exc).__name__}: {exc})"
        return done
    for point in brief.points[:BRIEF_POINTS]:
        source = done.report.sources[point.page - 1] if 1 <= point.page <= len(done.report.sources) else None
        if source is not None and quote_found(point.quote, source["text"]):
            done.points.append((point, source))
        else:
            done.dropped += 1
    done.missing = brief.missing.strip()
    return done


@dataclass
class ResearchReport:
    text: str
    pages: int  # pages that the worker judged
    credits: int
    searches: int  # searches that the back end made or that came from the store
    unavailable: str = ""  # set when no researcher could search: the reason


def research_text(found: list[Research]) -> str:
    lines = [f"Web research of {len(found)} product(s), one search each at the same time. The worker summed up the "
             "best parts of the relevant pages. Python found the quote of each point in its page."]
    for r in found:
        lines += ["", f"[id {r.product.listing_id}] {r.product.title[:100]}, search '{r.words}'"]
        if r.error and not r.points:
            lines.append(f"- not researched: {r.error}")
            continue
        if r.report is not None and not r.report.sources:
            lines.append("- " + r.report.text.split("\n", 1)[0] + " No page part was found about it.")
        for point, source in r.points:
            lines.append(f"- {point.text} \"{' '.join(point.quote.split())}\" ({source['site']}, {source['kind']}, "
                         f"published {source['published'] or 'not known'}, {source['url']})")
        if r.missing:
            lines.append(f"Not in the pages: {r.missing}")
        if r.dropped:
            lines.append(f"({r.dropped} point(s) left out: the quote was not in the page.)")
        if r.error:
            lines.append(f"({r.error})")
    return "\n".join(lines)


def research(items: list[tuple[ProductRef, str]], client, llm, store: WebStore,
             rerank: Callable[[str, list[str]], list[float]], max_pages: int, want: str = "",
             extract: Callable[[list[str]], dict[str, str]] | None = None,
             workers: int = RESEARCHERS, lock=None) -> ResearchReport:
    """One researcher for each (product, search words), RESEARCHERS at the same time. The database and the reranker
    are used by one researcher at a time. lock: the lock of the database connection, when the caller has one."""
    lock = lock or threading.Lock()

    def locked(fn):
        def call(*args):
            with lock:
                return fn(*args)
        return call
    shared = WebStore(locked(store.stored), locked(store.save_search), locked(store.save_chunks))
    with ThreadPoolExecutor(max(1, min(workers, len(items)))) as pool:
        futures = [pool.submit(contextvars.copy_context().run, research_one, product, words, client, llm, shared,
                               locked(rerank), max_pages, want, extract) for product, words in items]
        found = [future.result() for future in futures]
    reports = [r.report for r in found if r.report is not None]
    unavailable = "; ".join(sorted({r.error for r in found if r.unavailable}))
    return ResearchReport(research_text(found), sum(r.pages for r in reports), sum(r.credits for r in reports),
                          len(reports), unavailable if found and all(r.unavailable for r in found) else "")
