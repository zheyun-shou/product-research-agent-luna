"""amazon.se URLs and the keys used to file snapshots."""
import hashlib
import re
from decimal import Decimal
from urllib.parse import parse_qs, urlencode, urlsplit

BASE_URL = "https://www.amazon.se"
SOURCE = "amazon_se"
_ASIN = re.compile(r"/dp/([A-Z0-9]{10})")
_REVIEWS = re.compile(r"/product-reviews/([A-Z0-9]{10})")
REVIEW_FILTERS = ("positive", "critical")  # the star filters of the review pages: 4 and 5 stars, 1 to 3 stars
# amazon.se ignores pageNumber in the URL (seen on 2026-10-06: page 2 gave the reviews of page 1). The more button
# loads further reviews with a script. A second sort order gives other reviews with a plain URL.
REVIEW_SORTS = ("top", "recent")


# The sort orders of the search, read from the sort menu on 2026-10-06. The default order has no parameter.
SEARCH_SORTS = {"rating": "review-rank", "sales": "exact-aware-popularity-rank"}


def search_url(query: str, page: int = 1, sort: str | None = None, filters: list[str] = ()) -> str:
    """filters: option values "key:value" (see search.parse_filters) and the price range of price_filter."""
    params = {"k": query}
    if filters:
        params["rh"] = refinements(filters)
    if page > 1:
        params["page"] = str(page)
    if sort:
        params["s"] = SEARCH_SORTS[sort]
    return f"{BASE_URL}/s?{urlencode(params)}"


def refinements(filters: list[str]) -> str:
    """The rh parameter: the options of one group are joined with |, the groups with commas."""
    groups: dict[str, list[str]] = {}
    for f in filters:
        key, _, value = f.partition(":")
        groups.setdefault(key, [])
        if value not in groups[key]:
            groups[key].append(value)
    return ",".join(f"{key}:{'|'.join(values)}" for key, values in groups.items())


def price_filter(budget: Decimal | None) -> list[str]:
    """The price range up to the budget. amazon.se counts it in öre."""
    return [f"p_36:-{int(budget * 100)}"] if budget is not None else []


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
    sort = f"-{query['s'][0]}" if "s" in query else ""
    # A short hash of the filters keeps filtered and unfiltered pages apart
    rh = f"-f{hashlib.sha1(query['rh'][0].encode()).hexdigest()[:8]}" if "rh" in query else ""
    return f"{query.get('k', ['home'])[0]}-p{query.get('page', ['1'])[0]}{sort}{rh}"
