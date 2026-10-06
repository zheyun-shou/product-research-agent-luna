"""amazon.se URLs and the keys used to file snapshots."""
import re
from urllib.parse import parse_qs, urlencode, urlsplit

BASE_URL = "https://www.amazon.se"
SOURCE = "amazon_se"
_ASIN = re.compile(r"/dp/([A-Z0-9]{10})")
_REVIEWS = re.compile(r"/product-reviews/([A-Z0-9]{10})")
REVIEW_FILTERS = ("positive", "critical")  # the star filters of the review pages: 4 and 5 stars, 1 to 3 stars
# amazon.se ignores pageNumber in the URL (seen on 2026-10-06: page 2 gave the reviews of page 1). The more button
# loads further reviews with a script. A second sort order gives other reviews with a plain URL.
REVIEW_SORTS = ("top", "recent")


def search_url(query: str, page: int = 1) -> str:
    params = {"k": query}
    if page > 1:
        params["page"] = str(page)
    return f"{BASE_URL}/s?{urlencode(params)}"


def product_url(asin: str) -> str:
    return f"{BASE_URL}/dp/{asin}"


def reviews_url(asin: str, star: str, sort: str = "top") -> str:
    """The first review page of a product, with a star filter and a sort order. amazon.se shows these pages only
    after a login."""
    if star not in REVIEW_FILTERS or sort not in REVIEW_SORTS:
        raise ValueError(f"unknown review filter or sort order {star!r}, {sort!r}")
    params = {"filterByStar": star, "reviewerType": "all_reviews", "pageNumber": "1"}
    if sort != "top":
        params["sortBy"] = sort
    return f"{BASE_URL}/product-reviews/{asin}/?{urlencode(params)}"


def asin_from_reviews_url(url: str) -> str | None:
    match = _REVIEWS.search(url)
    return match.group(1) if match else None


def asin_from_url(url: str) -> str | None:
    match = _ASIN.search(url)
    return match.group(1) if match else None


def page_kind(url: str) -> str:
    if asin_from_reviews_url(url):
        return "reviews"
    if asin_from_url(url):
        return "product"
    if urlsplit(url).path == "/s":
        return "search"
    return "other"


def snapshot_key(url: str) -> str:
    query = parse_qs(urlsplit(url).query)
    asin = asin_from_reviews_url(url)
    if asin:
        return f"{asin}-{query.get('filterByStar', ['all'])[0]}-{query.get('sortBy', ['top'])[0]}"
    asin = asin_from_url(url)
    if asin:
        return asin
    return f"{query.get('k', ['home'])[0]}-p{query.get('page', ['1'])[0]}"
