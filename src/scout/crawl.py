"""One crawl: search pages, then detail pages, each checked, parsed and stored."""
import contextvars
import threading
from collections import Counter
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable

import psycopg

from scout.db.store import (known_source_ids, load_filters, log_fetch, refresh_listings, save_filters, save_record,
                             stored_attributes)
from scout.llm.client import LLMOutputError, LLMTimeout, LLMUnavailable
from scout.search.threshold import Page, ResultList, threshold_search
from scout.sources.base import Candidate, FilterGroup, ProductRecord, RawPage, SessionStatus

OnProblem = Callable[[SessionStatus, str], bool]
Enricher = Callable[[ProductRecord], "tuple[ProductRecord, bool]"]
Screener = Callable[[list[Candidate]], list[Candidate]]
FilterChooser = Callable[[list[FilterGroup]], list[str]]
CardCheck = Callable[[Candidate], bool]
TOP_K = 10  # the Threshold Algorithm stops when the best 10 candidates are known
MAX_ATTEMPTS = 3
EXTRACT_WORKERS = 4  # LLM extractions that run while the browser loads the next pages


@dataclass
class CrawlReport:
    search_pages: int = 0
    candidates: int = 0
    over_budget: int = 0
    off_topic: int = 0
    known: int = 0
    dropped: list[Candidate] = field(default_factory=list)
    detail_pages: int = 0
    saved: int = 0
    llm_pages: int = 0
    skipped: list[str] = field(default_factory=list)
    missing: Counter = field(default_factory=Counter)
    notes: list[str] = field(default_factory=list)
    unread: list[Candidate] = field(default_factory=list)  # kept candidates past max_details, in order
    off_condition: int = 0
    filters: list[str] = field(default_factory=list)  # the filters of the site that the search used
    list_pages: dict[str, int] = field(default_factory=dict)  # the result pages read in each list
    stop: str = ""  # why the Threshold Algorithm stopped
    # What happened to each candidate (source id): over_budget, off_condition, off_topic, stored_before, read,
    # not_loaded, not_parsed or unread. The hidden-target test finds where its product left the search.
    outcomes: dict[str, str] = field(default_factory=dict)


def fetch_checked(
    adapter, conn: psycopg.Connection, url: str, on_problem: OnProblem, notes: list[str] | None = None,
) -> RawPage | None:
    """Fetch and check one page. Ask the user to fix a problem, then try the same URL again.

    The last attempt does not ask, because no fetch would follow the answer.
    An incomplete page and a server error (503) are fetched again without asking, after the polite gap of the
    browser (about 5 seconds).
    """
    for attempt in range(1, MAX_ATTEMPTS + 1):
        page = adapter.fetch(url)
        status = adapter.check_page(page)
        log_fetch(conn, adapter.name, url, "ok" if status.ok else status.problem,
                  page.status, page.duration_ms, status.detail)
        if status.ok:
            return page
        if status.problem == "gone":
            return None
        if attempt == MAX_ATTEMPTS:
            if notes is not None:
                notes.append(f"gave up after {MAX_ATTEMPTS} attempts ({status.problem}) at {url}")
            return None
        if status.problem not in ("incomplete", "unavailable") and not on_problem(status, url):
            return None
    return None


def crawl(
    conn: psycopg.Connection,
    adapter,
    category: str,
    query: str,
    *,
    on_problem: OnProblem,
    max_pages: int = 1,
    max_details: int = 20,
    budget: Decimal | None = None,
    enrich: Enricher | None = None,
    screen: Screener | None = None,
    first_page: int = 1,
    skip_known: bool = False,
    sorts: tuple[str, ...] = (),
    filters: FilterChooser | None = None,
    card_check: CardCheck | None = None,
    k: int = TOP_K,
) -> CrawlReport:
    """sorts: also read these sort orders of the site, for example ("rating", "sales"). filters: chooses the filters
    of the site from its filter groups; the filtered lists come first in the read order, then the unfiltered default
    order (a product without the tag of the site is not in a filtered list). The Threshold Algorithm decides how many
    of the max_pages pages of each list to read. card_check: True for a card whose values are outside the needs.
    The detail pages keep the limit max_details. The kept candidates past the limit are in report.unread, for
    read_details later."""
    if not getattr(adapter, "terms_check", ""):
        raise RuntimeError(f"Adapter {adapter.name} has no terms check recorded, so it stays disabled.")
    report = CrawlReport()
    loaded: dict[tuple, Page] = {}

    def fetcher(sort: str | None, chosen: list[str], name: str):
        def fetch(number: int) -> Page | None:
            key = (sort, tuple(chosen), number)
            if key in loaded:
                return loaded[key]
            url = adapter.search_url(query, number, sort, chosen)
            page = fetch_checked(adapter, conn, url, on_problem, report.notes)
            if page is None:
                report.notes.append(f"the {name} order did not load at page {number}: {url}")
                return None
            report.search_pages += 1
            if hasattr(adapter, "parse_filters"):
                save_filters(conn, adapter.name, category, adapter.parse_filters(page))
            loaded[key] = adapter.parse_search(page)
            return loaded[key]
        return fetch

    chosen: list[str] = []
    if filters is not None and hasattr(adapter, "parse_filters"):
        groups = load_filters(conn, adapter.name, category)
        if not groups:  # the first search of the category: its page gives the filters
            fetcher(None, [], "default")(first_page)
            groups = load_filters(conn, adapter.name, category)
        chosen = filters(groups)
    report.filters = chosen
    orders = [None, *sorts]
    if chosen:
        lists = [ResultList(f"{s or 'default'} with filters", fetcher(s, chosen, f"{s or 'default'} with filters"))
                 for s in orders]
        lists.append(ResultList("default", fetcher(None, [], "default"), first=False))
    else:
        lists = [ResultList(s or "default", fetcher(s, [], s or "default")) for s in orders]
    found = threshold_search(lists, k=k, max_pages=max_pages, first_page=first_page)
    report.list_pages, report.stop = found.pages, found.stop
    if found.failed and len(found.failed) == len(lists):
        report.notes.append(f"search stopped at page {first_page}: no result list loaded")
    candidates = found.order
    report.candidates = len(candidates)
    kept = []
    for c in candidates:
        if budget is not None and c.price is not None and c.price > budget:
            report.outcomes[c.source_id] = "over_budget"
        elif card_check is not None and card_check(c):
            report.outcomes[c.source_id] = "off_condition"
        else:
            kept.append(c)
    report.over_budget = sum(v == "over_budget" for v in report.outcomes.values())
    report.off_condition = sum(v == "off_condition" for v in report.outcomes.values())
    candidates = kept
    if screen is not None and candidates:
        try:
            kept = screen(candidates)
            report.off_topic = len(candidates) - len(kept)
            report.dropped = [c for c in candidates if c not in kept]
            report.outcomes.update({c.source_id: "off_topic" for c in report.dropped})
            candidates = kept
        except (LLMUnavailable, LLMOutputError) as exc:
            report.notes.append(f"Title screening skipped: {exc}")
    if skip_known and candidates:
        known = known_source_ids(conn, candidates[0].source, [c.source_id for c in candidates])
        report.known = len(known)
        report.outcomes.update({i: "stored_before" for i in known})
        refresh_listings(conn, [c for c in candidates if c.source_id in known])
        candidates = [c for c in candidates if c.source_id not in known]
    read_details(conn, adapter, category, candidates[:max_details], report, on_problem=on_problem, enrich=enrich)
    report.unread = candidates[max_details:]
    report.outcomes.update({c.source_id: "unread" for c in report.unread})
    return report


def read_details(conn: psycopg.Connection, adapter, category: str, candidates: list[Candidate], report: CrawlReport,
                 *, on_problem: OnProblem, enrich: Enricher | None = None) -> CrawlReport:
    """Fetch, parse and store the detail pages of candidates. The browser loads one page at a time; the LLM
    extraction of a page runs in a worker thread while the browser loads the next pages. The database is used only
    in this thread."""
    pending: list[tuple[Future, object, str]] = []
    state = {"enrich": enrich}
    stopped = threading.Event()  # the LLM is unavailable: the extractions not started yet do not call it

    def extract(rec):
        if stopped.is_set():
            return rec, False
        try:
            return enrich(rec)
        except LLMTimeout:
            raise
        except LLMUnavailable:
            stopped.set()
            raise

    def store(rec, url: str, future: Future | None) -> None:
        if future is not None:
            try:
                rec, called = future.result()
                report.llm_pages += int(called)
            except LLMTimeout as exc:  # a slow call on one page: the next pages still use the LLM
                report.notes.append(f"{url}: {exc}. Stored without LLM extraction.")
            except LLMUnavailable as exc:
                if state["enrich"] is not None:
                    report.notes.append(f"{exc} The remaining pages are stored without LLM extraction.")
                state["enrich"] = None
            except LLMOutputError as exc:
                report.notes.append(f"{url}: {exc}. Stored without LLM extraction.")
            except Exception as exc:  # an error in one extraction must not lose the page
                report.notes.append(f"{url}: extraction failed ({type(exc).__name__}: {exc}). "
                                    "Stored without LLM extraction.")
        save_record(conn, rec, category)
        report.saved += 1
        report.missing.update(rec.missing_core())

    def store_done(wait: bool) -> None:
        while pending and (wait or pending[0][0].done()):
            future, rec, url = pending.pop(0)
            store(rec, url, future)

    with ThreadPoolExecutor(EXTRACT_WORKERS) as pool:
        for cand in candidates:
            page = fetch_checked(adapter, conn, cand.url, on_problem, report.notes)
            if page is None:
                report.skipped.append(cand.url)
                report.outcomes[cand.source_id] = "not_loaded"
                store_done(wait=False)
                continue
            report.detail_pages += 1
            try:
                rec = adapter.parse(page)
            except ValueError as exc:
                report.skipped.append(cand.url)
                report.outcomes[cand.source_id] = "not_parsed"
                report.notes.append(str(exc))
                continue
            report.outcomes[cand.source_id] = "read"
            if state["enrich"] is None or stopped.is_set():
                store(rec, cand.url, None)
            else:
                stored = stored_attributes(conn, rec.source, rec.source_id)
                rec = rec.model_copy(update={"attributes": {**stored, **rec.attributes}})
                # The worker thread keeps the trace span of this search
                pending.append((pool.submit(contextvars.copy_context().run, extract, rec), rec, cand.url))
            store_done(wait=False)
        store_done(wait=True)
    return report
