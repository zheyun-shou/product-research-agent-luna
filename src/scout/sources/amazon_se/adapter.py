"""The amazon.se adapter: fetch through the browser, snapshot, check and parse."""
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from scout.sources.amazon_se.check import check_page
from scout.sources.amazon_se.product import parse_product_page
from scout.sources.amazon_se.reviews import parse_review_page
from scout.sources.amazon_se.search import has_next_page, parse_filters, parse_search_page
from scout.sources.amazon_se.urls import (BASE_URL, SOURCE, page_kind, price_filter, reviews_url, search_url,
                                         snapshot_key)
from scout.sources.base import Candidate, FilterGroup, ProductRecord, RawPage, SessionStatus
from scout.sources.snapshots import save_html


class Browser(Protocol):
    def fetch(self, url: str) -> RawPage: ...
    def screenshot(self, path: Path) -> Path: ...


class AmazonSeAdapter:
    name = SOURCE
    kind = "product"
    access = "user_browser"
    terms_check = (
        "2026-10-03: I accept personal, low-volume fetching of amazon.se "
        "in my own browser profile (spec section 8)."
    )

    def __init__(self, browser: Browser, snapshot_dir: Path, screenshots: bool = False):
        self.browser = browser
        self.snapshot_dir = snapshot_dir
        self.screenshots = screenshots

    sorts = ("rating", "sales")  # the floor of the agent loop: page 1 of these sort orders too

    def search_url(self, query: str, page: int = 1, sort: str | None = None, filters: list[str] = ()) -> str:
        return search_url(query, page, sort, filters)

    def reviews_url(self, asin: str, star: str, sort: str = "top") -> str:
        return reviews_url(asin, star, sort)

    def fetch(self, url: str) -> RawPage:
        page = self.browser.fetch(url)
        kind = page_kind(url)
        path = save_html(page, self.snapshot_dir, SOURCE, kind, snapshot_key(url))
        if self.screenshots and kind == "product":
            self.browser.screenshot(path.with_suffix(".png"))
        return page.model_copy(update={"snapshot_path": str(path)})

    def check_page(self, page: RawPage) -> SessionStatus:
        return check_page(page)

    def check_session(self) -> SessionStatus:
        return check_page(self.browser.fetch(BASE_URL + "/"))

    def parse_search(self, page: RawPage) -> tuple[list[Candidate], bool]:
        return parse_search_page(page.html), has_next_page(page.html)

    def parse_filters(self, page: RawPage) -> list[FilterGroup]:
        return parse_filters(page.html)

    def price_filter(self, budget: Decimal | None) -> list[str]:
        return price_filter(budget)

    def parse(self, page: RawPage) -> ProductRecord:
        return parse_product_page(page.html, page.url, page.fetched_at)

    def parse_reviews(self, page: RawPage) -> tuple[list[dict], int | None]:
        return parse_review_page(page.html)
