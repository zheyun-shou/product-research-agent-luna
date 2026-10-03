"""Read amazon.se product pages with hand-written selectors."""
import re
from datetime import datetime

from bs4 import BeautifulSoup, Tag

from scout.sources.amazon_se.numbers import parse_count, parse_price, parse_rating
from scout.sources.amazon_se.urls import SOURCE, asin_from_url, product_url
from scout.sources.base import ProductRecord

BRAND_KEYS = ("Märke", "Varumärke", "Brand")
# Modellnamn is left out. It is often a marketing name, and the store matches products on the model.
MODEL_KEYS = ("Modellnummer", "Artikelns modellnummer", "Modell", "Item model number", "Model number")
PRICE_AREAS = (
    "#corePrice_feature_div .a-price",
    "#corePriceDisplay_desktop_feature_div .priceToPay",
    "#corePriceDisplay_desktop_feature_div .a-price",
    "#apex_desktop .a-price",
)
SPEC_ROWS = (
    "#productDetails_techSpec_section_1 tr",
    "#productDetails_techSpec_section_2 tr",
    "#productDetails_detailBullets_sections1 tr",
    "#productOverview_feature_div tr",
    "table.prodDetTable tr",
)
UNAVAILABLE = (
    "inte tillgänglig", "tillfälligt slut", "slut i lager", "inte i lager", "ej i lager",
    "currently unavailable", "out of stock",
)
BYLINE = (r"Varumärke:\s*(.+)", r"Besök (.+?)-butiken", r"Visit the (.+?) Store", r"Brand:\s*(.+)")
MAX_TEXT = 12_000
_MARKS = dict.fromkeys(map(ord, "\u200e\u200f"), None)


def parse_product_page(html: str, url: str, fetched_at: datetime) -> ProductRecord:
    soup = BeautifulSoup(html, "lxml")
    asin = asin_from_url(url) or _hidden_asin(soup)
    if not asin:
        raise ValueError(f"No ASIN in the URL or page: {url}")
    specs = _specs(soup)
    bullets = [t for t in (_text(li) for li in soup.select("#feature-bullets li")) if t]
    price, currency = _price(soup)
    rating_el = soup.select_one("#acrPopover")
    rating_text = (rating_el.get("title") or _text(rating_el)) if rating_el else ""
    count_el = soup.select_one("#acrCustomerReviewText")
    return ProductRecord(
        source=SOURCE,
        source_id=asin,
        url=product_url(asin),
        fetched_at=fetched_at,
        title=_text(soup.select_one("#productTitle")) or None,
        brand=_first(specs, BRAND_KEYS) or _brand_from_byline(soup),
        model=_first(specs, MODEL_KEYS),
        price=price,
        currency=currency,
        rating=parse_rating(rating_text) if rating_text else None,
        review_count=parse_count(_text(count_el)) if count_el else None,
        in_stock=_in_stock(soup),
        variant_label=" / ".join(
            _text(el) for el in soup.select('[id^="variation_"] .selection, .inline-twister-dim-title-value')
        ),
        specs=specs,
        bullets=bullets,
        text=_page_text(soup, specs, bullets),
    )


def _text(el: Tag | None) -> str:
    if el is None:
        return ""
    return " ".join(el.get_text(" ", strip=True).translate(_MARKS).split())


def _hidden_asin(soup: BeautifulSoup) -> str | None:
    el = soup.select_one("input#ASIN")
    return (el.get("value") or "").strip() or None if el else None


def _clean_key(key: str) -> str:
    return re.sub(r"\s*:\s*$", "", key).strip()


def _specs(soup: BeautifulSoup) -> dict[str, str]:
    specs: dict[str, str] = {}
    for selector in SPEC_ROWS:
        for row in soup.select(selector):
            cells = row.find_all(["th", "td"])
            if len(cells) >= 2:
                key, value = _clean_key(_text(cells[0])), _text(cells[1])
                if key and value:
                    specs.setdefault(key, value)
    for bold in soup.select("#detailBullets_feature_div li .a-text-bold"):
        value_el = bold.find_next_sibling("span")
        key, value = _clean_key(_text(bold)), _text(value_el)
        if key and value:
            specs.setdefault(key, value)
    return specs


def _first(specs: dict[str, str], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        if specs.get(key):
            return specs[key]
    return None


def _brand_from_byline(soup: BeautifulSoup) -> str | None:
    byline = _text(soup.select_one("#bylineInfo"))
    for pattern in BYLINE:
        match = re.search(pattern, byline)
        if match:
            return match.group(1).strip()
    return None


def _price(soup: BeautifulSoup):
    for selector in PRICE_AREAS:
        for el in soup.select(selector):
            if el.get("data-a-strike") == "true" or "a-text-price" in (el.get("class") or []):
                continue
            off = el.select_one(".a-offscreen")
            text = _text(off) or _price_from_parts(el)
            if text:
                price, currency = parse_price(text)
                if price is not None:
                    return price, currency
    return None, None


def _price_from_parts(el: Tag) -> str:
    whole = el.select_one(".a-price-whole")
    if whole is None:
        return ""
    fraction = el.select_one(".a-price-fraction")
    symbol = el.select_one(".a-price-symbol")
    parts = [whole.get_text("", strip=True), fraction.get_text("", strip=True) if fraction else ""]
    return "".join(parts) + " " + (symbol.get_text(strip=True) if symbol else "")


def _in_stock(soup: BeautifulSoup) -> bool | None:
    availability = _text(soup.select_one("#availability")).lower()
    if any(marker in availability for marker in UNAVAILABLE):
        return False
    if soup.select_one("#add-to-cart-button") is not None:
        return True
    if "i lager" in availability or "in stock" in availability:
        return True
    return None


def _page_text(soup: BeautifulSoup, specs: dict[str, str], bullets: list[str]) -> str:
    lines = [
        _text(soup.select_one(selector))
        for selector in (
            "#productTitle", "#bylineInfo", "#corePrice_feature_div",
            "#corePriceDisplay_desktop_feature_div", "#availability",
        )
    ]
    lines += [f"{key}: {value}" for key, value in specs.items()]
    lines += bullets
    lines.append(_text(soup.select_one("#productDescription")))
    return "\n".join(line for line in lines if line)[:MAX_TEXT]
