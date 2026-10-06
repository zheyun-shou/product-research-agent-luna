"""Shop reviews of the picks: the positive and the critical review pages, behind the login (phase 3 Task 5)."""
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable

import psycopg

from scout.crawl import OnProblem, fetch_checked
from scout.db.store import save_shop_reviews, shop_reviews_fetched

MAX_PICKS = 10
PAGES_PER_PICK = 3
MAX_PAGES = 30  # for each search
FRESH = timedelta(days=7)  # reviews younger than this are not read again


@dataclass
class ReviewReport:
    listings: int = 0  # listings whose review pages were read
    fresh: int = 0  # listings left out, because their reviews are younger than FRESH
    pages: int = 0
    reviews: Counter = field(default_factory=Counter)  # filter -> reviews found
    new: int = 0
    stopped: str = ""  # why the step stopped before the end
    notes: list[str] = field(default_factory=list)

    def text(self) -> str:
        if not self.pages and not self.stopped:
            return ""
        found = sum(self.reviews.values())
        line = (f"Read the shop reviews of {self.listings} picks on amazon.se: {self.pages} review pages, "
                f"{found} reviews ({self.reviews['positive']} positive, {self.reviews['critical']} critical), "
                f"{self.new} of them new.")
        if self.fresh:
            line += f" {self.fresh} picks have reviews from the last {FRESH.days} days, so they were not read again."
        if self.stopped:
            line += f" {self.stopped}"
        return "\n".join([line, *self.notes])


def page_plan(more: dict[str, bool], done: list[tuple[str, str]]) -> tuple[str, str] | None:
    """The next review page of one product: the top positive reviews, the top critical reviews, then the newest
    critical reviews when the filter has more reviews than the first page showed. amazon.se ignores the page number
    in the URL, so the third page uses the other sort order. The complaints say more about a product than the praise."""
    for first in (("positive", "top"), ("critical", "top")):
        if first not in done:
            return first
    if len(done) < PAGES_PER_PICK and more.get("critical") and ("critical", "recent") not in done:
        return "critical", "recent"
    return None


def read_reviews(conn: psycopg.Connection, adapter: Callable[[], object], targets: list[tuple[int, str]],
                 on_problem: OnProblem, max_pages: int = MAX_PAGES, now: datetime | None = None,
                 say: Callable[[str], None] = lambda text: None) -> ReviewReport:
    """Read the review pages of each (listing_id, ASIN) and store the reviews. adapter() opens the browser only
    when a page must be read. When the shopper skips a login or a captcha, the step stops."""
    report = ReviewReport()
    targets = targets[:MAX_PICKS]
    now = now or datetime.now(timezone.utc)
    fetched = shop_reviews_fetched(conn, [listing_id for listing_id, _ in targets])
    skipped = []

    def asked(status, url) -> bool:
        fixed = on_problem(status, url)
        if not fixed:
            skipped.append(url)
        return fixed

    for listing_id, asin in targets:
        if listing_id in fetched and now - fetched[listing_id] < FRESH:
            report.fresh += 1
            continue
        if not report.pages and not report.listings:
            say("Reading the positive and the critical shop reviews of the picks on amazon.se. "
                "This needs the login in the product-scout Chrome window.")
        source = adapter()
        found: dict[str, dict] = {}
        done: list[tuple[str, str]] = []
        more: dict[str, bool] = {}
        fetched_at = now
        while (step := page_plan(more, done)) is not None:
            if report.pages >= max_pages:
                report.stopped = f"Stopped at the limit of {max_pages} review pages for one search."
                break
            star, sort = step
            done.append(step)
            page = fetch_checked(source, conn, source.reviews_url(asin, star, sort), asked, report.notes)
            if page is None:
                if skipped:
                    break
                continue
            report.pages += 1
            fetched_at = page.fetched_at
            reviews, matching = source.parse_reviews(page)
            more[star] = matching is not None and matching > len(reviews)
            for review in reviews:
                found.setdefault(review["review_id"], {**review, "filter": star})
        if found:
            report.new += save_shop_reviews(conn, listing_id, list(found.values()), fetched_at)
            report.reviews.update(r["filter"] for r in found.values())
        if done:
            report.listings += 1
        if skipped:
            report.stopped = "You skipped the login, so the other review pages were not read."
            break
        if report.stopped:
            break
    return report
