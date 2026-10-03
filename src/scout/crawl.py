"""One crawl: search pages, then detail pages, each checked, parsed and stored."""
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable

import psycopg

from scout.db.store import log_fetch, save_record
from scout.sources.base import ProductRecord, RawPage, SessionStatus

OnProblem = Callable[[SessionStatus, str], bool]
Enricher = Callable[[ProductRecord], "tuple[ProductRecord, bool]"]
MAX_ATTEMPTS = 3


@dataclass
class CrawlReport:
    search_pages: int = 0
    candidates: int = 0
    over_budget: int = 0
    detail_pages: int = 0
    saved: int = 0
    llm_pages: int = 0
    skipped: list[str] = field(default_factory=list)
    missing: Counter = field(default_factory=Counter)
    notes: list[str] = field(default_factory=list)


def fetch_checked(adapter, conn: psycopg.Connection, url: str, on_problem: OnProblem) -> RawPage | None:
    for _ in range(MAX_ATTEMPTS):
        page = adapter.fetch(url)
        status = adapter.check_page(page)
        log_fetch(conn, adapter.name, url, "ok" if status.ok else status.problem,
                  page.status, page.duration_ms, status.detail)
        if status.ok:
            return page
        if status.problem == "gone" or not on_problem(status, url):
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
) -> CrawlReport:
    if not getattr(adapter, "terms_check", ""):
        raise RuntimeError(f"Adapter {adapter.name} has no terms check recorded, so it stays disabled.")
    report = CrawlReport()
    candidates, seen = [], set()
    for number in range(1, max_pages + 1):
        page = fetch_checked(adapter, conn, adapter.search_url(query, number), on_problem)
        if page is None:
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
    for cand in candidates[:max_details]:
        page = fetch_checked(adapter, conn, cand.url, on_problem)
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
            rec, called = enrich(rec)
            report.llm_pages += int(called)
        save_record(conn, rec, category)
        report.saved += 1
        report.missing.update(rec.missing_core())
    return report
