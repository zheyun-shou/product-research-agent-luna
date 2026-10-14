"""One crawl: search pages, then detail pages, each checked, parsed and stored."""
import contextvars
import threading
from collections import Counter
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable

import psycopg

from scout.db.store import known_source_ids, log_fetch, refresh_listings, save_record, stored_attributes
from scout.llm.client import LLMOutputError, LLMTimeout, LLMUnavailable
from scout.sources.base import Candidate, ProductRecord, RawPage, SessionStatus

OnProblem = Callable[[SessionStatus, str], bool]
Enricher = Callable[[ProductRecord], "tuple[ProductRecord, bool]"]
Screener = Callable[[list[Candidate]], list[Candidate]]
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
    # What happened to each candidate (source id): over_budget, off_topic, stored_before, read, not_loaded,
    # not_parsed or unread. The hidden-target test finds where its product left the search.
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


def interleave(orders: list[list[Candidate]]) -> list[Candidate]:
    """One list from the result lists of the sort orders: the first of each order, then the second of each, and so
    on, without repeats. So the best rated and the best selling results are in the first detail pages too."""
    merged, seen = [], set()
    for rank in range(max((len(o) for o in orders), default=0)):
        for order in orders:
            if rank < len(order) and order[rank].source_id not in seen:
                seen.add(order[rank].source_id)
                merged.append(order[rank])
    return merged


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
) -> CrawlReport:
    """sorts: also read the first result page of these sort orders of the site, for example ("rating", "sales").
    The orders take turns in the list of candidates; the detail pages keep the limit max_details. The kept
    candidates past the limit are in report.unread, for read_details later."""
    if not getattr(adapter, "terms_check", ""):
        raise RuntimeError(f"Adapter {adapter.name} has no terms check recorded, so it stays disabled.")
    report = CrawlReport()
    default: list[Candidate] = []
    for number in range(first_page, first_page + max_pages):
        url = adapter.search_url(query, number)
        page = fetch_checked(adapter, conn, url, on_problem, report.notes)
        if page is None:
            report.notes.append(f"search stopped at page {number}: {url} did not load")
            break
        report.search_pages += 1
        found, has_next = adapter.parse_search(page)
        default += found
        if not has_next:
            break
    orders = [default]
    for sort in sorts:
        url = adapter.search_url(query, 1, sort)
        page = fetch_checked(adapter, conn, url, on_problem, report.notes)
        if page is None:
            report.notes.append(f"the {sort} order did not load: {url}")
            continue
        report.search_pages += 1
        orders.append(adapter.parse_search(page)[0])
    candidates = interleave(orders)
    report.candidates = len(candidates)
    if budget is not None:
        within = []
        for c in candidates:
            if c.price is None or c.price <= budget:
                within.append(c)
            else:
                report.outcomes[c.source_id] = "over_budget"
        report.over_budget = len(candidates) - len(within)
        candidates = within
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
