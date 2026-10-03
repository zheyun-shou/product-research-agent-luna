"""Read amazon.se search result pages with hand-written selectors."""
from bs4 import BeautifulSoup, Tag

from scout.sources.amazon_se.numbers import parse_count, parse_price, parse_rating
from scout.sources.amazon_se.urls import SOURCE, product_url
from scout.sources.base import Candidate

CARD = 'div[data-component-type="s-search-result"]'


def parse_search_page(html: str) -> list[Candidate]:
    soup = BeautifulSoup(html, "lxml")
    found: list[Candidate] = []
    seen: set[str] = set()
    for card in soup.select(CARD):
        asin = (card.get("data-asin") or "").strip()
        title_el = card.select_one("h2 span") or card.select_one("h2")
        if not asin or asin in seen or title_el is None:
            continue
        seen.add(asin)
        price, currency = _price(card)
        rating_el = card.select_one(".a-icon-alt")
        count_el = card.select_one('a[href*="customerReviews"]')
        found.append(
            Candidate(
                source=SOURCE,
                source_id=asin,
                url=product_url(asin),
                title=title_el.get_text(" ", strip=True),
                price=price,
                currency=currency,
                rating=parse_rating(rating_el.get_text()) if rating_el else None,
                review_count=parse_count(count_el.get_text(" ", strip=True)) if count_el else None,
                sponsored=_is_sponsored(card),
            )
        )
    return found


def has_next_page(html: str) -> bool:
    nxt = BeautifulSoup(html, "lxml").select_one(".s-pagination-next")
    return nxt is not None and "s-pagination-disabled" not in (nxt.get("class") or [])


def _price(card: Tag):
    for el in card.select(".a-price"):
        if el.get("data-a-strike") == "true" or "a-text-price" in (el.get("class") or []):
            continue
        off = el.select_one(".a-offscreen")
        if off and off.get_text(strip=True):
            return parse_price(off.get_text())
    return None, None


def _is_sponsored(card: Tag) -> bool:
    if card.select_one(".puis-sponsored-label-text"):
        return True
    return any(span.get_text(strip=True) in ("Sponsrad", "Sponsored") for span in card.select("span"))
