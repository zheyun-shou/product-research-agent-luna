"""Read amazon.se search result pages with hand-written selectors."""
import re

from bs4 import BeautifulSoup, Tag

from scout.sources.amazon_se.numbers import parse_count, parse_price, parse_rating
from scout.sources.amazon_se.urls import SOURCE, product_url
from scout.sources.base import Candidate, FilterGroup, FilterOption

CARD = 'div[data-component-type="s-search-result"]'
# Groups that do not describe the product. Price has its own range parameter (see urls.price_filter).
SKIP_GROUPS = ("p_36", "p_6", "p_90", "p_98", "p_n_availability", "p_n_condition-type", "p_n_free_shipping_eligible",
               "p_n_deal_type")
_APPLY = re.compile(r"Apply the filter (.+) to narrow results|Använd filtret (.+) för att begränsa resultaten")
_NUMBER = r"(\d+(?:[.,]\d+)?)"
_UP_TO = re.compile(rf"^(?:Up to|Upp till|Under)\s+{_NUMBER}\s*(\S+)$", re.I)
_BETWEEN = re.compile(rf"^{_NUMBER}\s*(?:[^\s\d-]+\s+)?(?:(?:to|till)\s+|[-–]\s*){_NUMBER}\s*(\S+)$", re.I)
_ABOVE = re.compile(rf"^{_NUMBER}\s*(\S+)\s*(?:&|och)\s*(?:above|över|mer)$", re.I)


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
        found.append(
            Candidate(
                source=SOURCE,
                source_id=asin,
                url=product_url(asin),
                title=title_el.get_text(" ", strip=True),
                price=price,
                currency=currency,
                rating=parse_rating(rating_el.get_text()) if rating_el else None,
                review_count=_review_count(card),
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


def _review_count(card: Tag) -> int | None:
    # The star rating can link to the reviews too. Its text is a rating, not a count.
    for link in card.select('a[href*="customerReviews"]'):
        text = link.get_text(" ", strip=True)
        if text and parse_rating(text) is None:
            return parse_count(text)
    return None


def _is_sponsored(card: Tag) -> bool:
    if card.select_one(".puis-sponsored-label-text"):
        return True
    return any(span.get_text(strip=True) in ("Sponsrad", "Sponsored") for span in card.select("span"))


def parse_filters(html: str) -> list[FilterGroup]:
    """The filter groups of the left column of a search page, with their options. A number option ("48 to 60.9 cm",
    "Up to 47.9 cm", "112 cm & above") gets its range. Groups about delivery, sellers and stock are left out."""
    soup = BeautifulSoup(html, "lxml")
    groups = []
    for ul in soup.select('#s-refinements ul[id^="filter-"]'):
        key = ul["id"].removeprefix("filter-")
        title = soup.find(id=f"{key}-title")
        if key in SKIP_GROUPS or title is None:
            continue
        options = []
        for li in ul.find_all("li", id=True):
            item_key, _, value = li["id"].partition("/")
            link = li.find("a")
            if item_key != key or not value or link is None:
                continue
            label = _APPLY.match(link.get("aria-label") or "")
            name = next((g for g in label.groups() if g), "") if label else li.get_text(" ", strip=True)
            if name:
                options.append(FilterOption(name=name, value=f"{key}:{value}", **_range(name)))
        if options:
            groups.append(FilterGroup(key=key, name=title.get_text(" ", strip=True), options=options))
    return groups


def _range(name: str) -> dict:
    def number(text: str) -> float:
        return float(text.replace(",", "."))
    if m := _UP_TO.match(name):
        return {"high": number(m[1]), "unit": m[2]}
    if m := _BETWEEN.match(name):
        return {"low": number(m[1]), "high": number(m[2]), "unit": m[3]}
    if m := _ABOVE.match(name):
        return {"low": number(m[1]), "unit": m[2]}
    return {}
