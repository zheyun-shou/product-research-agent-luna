"""amazon.se URLs and the keys used to file snapshots."""
import re
from urllib.parse import parse_qs, urlencode, urlsplit

BASE_URL = "https://www.amazon.se"
SOURCE = "amazon_se"
_ASIN = re.compile(r"/dp/([A-Z0-9]{10})")


def search_url(query: str, page: int = 1) -> str:
    params = {"k": query}
    if page > 1:
        params["page"] = str(page)
    return f"{BASE_URL}/s?{urlencode(params)}"


def product_url(asin: str) -> str:
    return f"{BASE_URL}/dp/{asin}"


def asin_from_url(url: str) -> str | None:
    match = _ASIN.search(url)
    return match.group(1) if match else None


def page_kind(url: str) -> str:
    if asin_from_url(url):
        return "product"
    if urlsplit(url).path == "/s":
        return "search"
    return "other"


def snapshot_key(url: str) -> str:
    asin = asin_from_url(url)
    if asin:
        return asin
    query = parse_qs(urlsplit(url).query)
    return f"{query.get('k', ['home'])[0]}-p{query.get('page', ['1'])[0]}"
