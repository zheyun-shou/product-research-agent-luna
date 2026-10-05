"""One crawl: search pages, then detail pages, each checked, parsed and stored."""
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable

import psycopg

from scout.db.store import known_source_ids, log_fetch, refresh_listings, save_record, stored_attributes
from scout.llm.client import LLMOutputError, LLMUnavailable
from scout.sources.base import Candidate, ProductRecord, RawPage, SessionStatus

OnProblem = Callable[[SessionStatus, str], bool]
Enricher = Callable[[ProductRecord], "tuple[ProductRecord, bool]"]
Screener = Callable[[list[Candidate]], list[Candidate]]
MAX_ATTEMPTS = 3


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


def fetch_checked(
    adapter, conn: psycopg.Connection, url: str, on_problem: OnProblem, notes: list[str] | None = None,
) -> RawPage | None:
    """Fetch and check one page. Ask the user to fix a problem, then try the same URL again.

    The last attempt does not ask, because no fetch would follow the answer.
    An incomplete page is fetched again without asking.
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
        if status.problem != "incomplete" and not on_problem(status, url):
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
) -> CrawlReport:
    if not getattr(adapter, "terms_check", ""):
        raise RuntimeError(f"Adapter {adapter.name} has no terms check recorded, so it stays disabled.")
    report = CrawlReport()
    candidates, seen = [], set()
    for number in range(first_page, first_page + max_pages):
        url = adapter.search_url(query, number)
        page = fetch_checked(adapter, conn, url, on_problem, report.notes)
        if page is None:
            report.notes.append(f"search stopped at page {number}: {url} did not load")
            break
        report.search_pages += 1
        found, has_next = adapter.parse_search(page)
        for cand in found:
            if cand.source_id not in seen:
                seen.add(cand.source_id)
                candidates.append(cand)
        if not has_next:
            break
    report.candidates = len(candidates)
    if budget is not None:
        within = [c for c in candidates if c.price is None or c.price <= budget]
        report.over_budget = len(candidates) - len(within)
        candidates = within
    if screen is not None and candidates:
        try:
            kept = screen(candidates)
            report.off_topic = len(candidates) - len(kept)
            report.dropped = [c for c in candidates if c not in kept]
            candidates = kept
        except (LLMUnavailable, LLMOutputError) as exc:
            report.notes.append(f"Title screening skipped: {exc}")
    if skip_known and candidates:
        known = known_source_ids(conn, candidates[0].source, [c.source_id for c in candidates])
        report.known = len(known)
        refresh_listings(conn, [c for c in candidates if c.source_id in known])
        candidates = [c for c in candidates if c.source_id not in known]
    for cand in candidates[:max_details]:
        page = fetch_checked(adapter, conn, cand.url, on_problem, report.notes)
        if page is None:
            report.skipped.append(cand.url)
            continue
        report.detail_pages += 1
        try:
            rec = adapter.parse(page)
        except ValueError as exc:
            report.skipped.append(cand.url)
            report.notes.append(str(exc))
            continue
        if enrich is not None:
            stored = stored_attributes(conn, rec.source, rec.source_id)
            rec = rec.model_copy(update={"attributes": {**stored, **rec.attributes}})
            try:
                rec, called = enrich(rec)
                report.llm_pages += int(called)
            except LLMUnavailable as exc:
                report.notes.append(f"{exc} The remaining pages are stored without LLM extraction.")
                enrich = None
            except LLMOutputError as exc:
                report.notes.append(f"{cand.url}: {exc}. Stored without LLM extraction.")
        save_record(conn, rec, category)
        report.saved += 1
        report.missing.update(rec.missing_core())
    return report
