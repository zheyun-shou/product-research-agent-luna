"""The generic reader of a product page of any shop (design section 1a): the schema.org Product data in the page
(JSON-LD) first. The worker fills what is missing from the page text later, with the raw-text check."""
import json
import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from scout.fetch import site_of
from scout.sources.amazon_se.numbers import to_decimal
from scout.sources.base import ProductRecord, RawPage

MAX_REVIEWS = 30
MAX_TEXT = 30_000
OLD_PRICES = {"StrikethroughPrice", "ListPrice", "SRP", "MSRP"}  # schema.org price types that are not the price now


def _types(item: dict) -> set[str]:
    kind = item.get("@type") or []
    return {str(k).rsplit("/", 1)[-1] for k in (kind if isinstance(kind, list) else [kind])}


def _items(data) -> list[dict]:
    """All objects of a JSON-LD block, also those in @graph and in lists."""
    if isinstance(data, list):
        return [i for d in data for i in _items(d)]
    if not isinstance(data, dict):
        return []
    return [data, *_items(data.get("@graph") or [])]


def json_ld(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    found = []
    for script in soup.find_all("script", type=re.compile(r"application/ld\+json", re.I)):
        text = (script.string or script.get_text() or "").strip()
        try:
            data = json.loads(text)
        except ValueError:
            # Some shops put raw line breaks in strings
            try:
                data = json.loads(re.sub(r"[\r\n\t]+", " ", text))
            except ValueError:
                continue
        found += _items(data)
    return found


def products_of(items: list[dict]) -> list[dict]:
    """The Product objects. A ProductGroup gives its first variant, with the fields of the group as defaults."""
    products = []
    for item in items:
        types = _types(item)
        if "Product" in types:
            products.append(item)
        elif "ProductGroup" in types:
            variants = [v for v in item.get("hasVariant") or [] if isinstance(v, dict)]
            products.append({**item, **variants[0]} if variants else item)
    return products


def _text(value) -> str | None:
    if isinstance(value, dict):
        value = value.get("name") or value.get("@id")
    if isinstance(value, list):
        value = value[0] if value else None
    text = " ".join(str(value).split()) if value not in (None, "") else ""
    return text or None


def _number(value) -> Decimal | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    text = str(value).strip()
    try:
        return Decimal(text)  # "1299.00" in the schema.org form
    except InvalidOperation:
        return to_decimal(text)


def _offer(product: dict) -> dict:
    offers = product.get("offers") or {}
    if isinstance(offers, list):
        offers = next((o for o in offers if isinstance(o, dict) and o.get("price") not in (None, "")),
                      offers[0] if offers else {})
    if not isinstance(offers, dict):
        return {}
    specs = offers.get("priceSpecification") or []
    specs = specs if isinstance(specs, list) else [specs]
    if offers.get("price") in (None, "") and specs:  # NetOnNet: the price now, and the old price struck through
        now = [s for s in specs if isinstance(s, dict) and s.get("price") not in (None, "")
               and (_text(s.get("priceType")) or "").rsplit("/", 1)[-1] not in OLD_PRICES]
        if now:
            return {**offers, "price": now[0]["price"],
                    "priceCurrency": offers.get("priceCurrency") or now[0].get("priceCurrency")}
    if "AggregateOffer" in _types(offers) and offers.get("price") in (None, ""):
        inner = offers.get("offers")
        if isinstance(inner, list) and inner and isinstance(inner[0], dict):
            return inner[0]
        return {**offers, "price": offers.get("lowPrice")}
    return offers


def _stock(availability) -> bool | None:
    word = (_text(availability) or "").rsplit("/", 1)[-1].lower()
    if word in {"instock", "onlineonly", "limitedavailability", "instoreonly", "presale", "preorder"}:
        return True
    if word in {"outofstock", "soldout", "discontinued"}:
        return False
    return None


def _rating(product: dict) -> tuple[Decimal | None, int | None]:
    agg = product.get("aggregateRating")
    if not isinstance(agg, dict):
        return None, None
    value = _number(agg.get("ratingValue"))
    best = _number(agg.get("bestRating")) or Decimal(5)
    if value is not None and best and best != 5:
        value = (value * 5 / best).quantize(Decimal("0.01"))
    count = _number(agg.get("reviewCount")) or _number(agg.get("ratingCount"))
    return value, int(count) if count is not None else None


def _reviews(product: dict) -> list[dict]:
    reviews = product.get("review") or []
    if isinstance(reviews, dict):
        reviews = [reviews]
    found = []
    for r in reviews[:MAX_REVIEWS]:
        if not isinstance(r, dict):
            continue
        text = _text(r.get("reviewBody") or r.get("description"))
        if not text:
            continue
        rating = r.get("reviewRating")
        value = _number(rating.get("ratingValue")) if isinstance(rating, dict) else None
        best = _number(rating.get("bestRating")) if isinstance(rating, dict) else None
        if value is not None and best and best != 5:
            value = (value * 5 / best).quantize(Decimal("0.1"))
        found.append({"title": _text(r.get("name")) or "", "rating": float(value) if value is not None else None,
                      "date": _text(r.get("datePublished")) or "", "verified": False, "text": text})
    return found  # never the author


def _images(product: dict, base: str) -> list[str]:
    images = product.get("image") or []
    images = images if isinstance(images, list) else [images]
    urls = [i.get("url") or i.get("contentUrl") if isinstance(i, dict) else i for i in images]
    return [urljoin(base, u) for u in urls if isinstance(u, str) and u][:12]


def _specs(product: dict) -> dict[str, str]:
    props = product.get("additionalProperty") or []
    props = props if isinstance(props, list) else [props]
    specs = {}
    for p in props:
        if isinstance(p, dict) and _text(p.get("name")) and p.get("value") not in (None, ""):
            value = _text(p.get("value"))
            unit = _text(p.get("unitText") or p.get("unitCode"))
            specs[_text(p.get("name"))] = f"{value} {unit}" if unit and unit not in (value or "") else value
    return specs


GTIN_KEYS = ("gtin13", "gtin", "gtin12", "gtin14", "gtin8", "ean")


def gtin_of(product: dict) -> str | None:
    for key in GTIN_KEYS:
        digits = re.sub(r"\D", "", _text(product.get(key)) or "")
        if len(digits) in (8, 12, 13, 14):
            return digits.zfill(14)  # one form for EAN-13, UPC-A and GTIN-14
    return None


NOT_CONTENT = ["script", "style", "noscript", "template", "svg", "nav", "header", "footer", "aside", "iframe"]


def shop_text(html: str) -> str:
    """The visible text of a shop page without the menus, the header and the footer, one block on a line. The
    specifications of shops are in tables and lists that an article extractor drops (Elgiganten, 2026-10-07)."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(NOT_CONTENT):
        tag.decompose()
    lines, last = [], None
    for line in soup.get_text("\n").splitlines():
        line = " ".join(line.split())
        if line and line != last:
            lines.append(line)
            last = line
    return "\n".join(lines)[:MAX_TEXT]


def page_text(html: str, url: str) -> str:
    """The main text of an article (a test, a forum page), without the menus, through trafilatura."""
    import trafilatura
    text = trafilatura.extract(html, url=url, include_tables=True, include_comments=False, favor_recall=True)
    return (text or "")[:MAX_TEXT]


def canonical(page: RawPage) -> str:
    soup = BeautifulSoup(page.html, "lxml")
    link = soup.find("link", rel="canonical")
    href = link.get("href") if link else None
    url = urljoin(page.final_url, href) if href else page.final_url
    parts = urlparse(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


def read_generic(page: RawPage) -> tuple[ProductRecord, list[str]]:
    """The record of one shop page, and the fields that came from its JSON-LD."""
    url = canonical(page)
    products = products_of(json_ld(page.html))
    product = products[0] if products else {}
    offer = _offer(product)
    rating, count = _rating(product)
    sku = _text(product.get("sku")) or _text(offer.get("sku"))
    text = shop_text(page.html)
    description = _text(product.get("description")) or ""
    rec = ProductRecord(
        source=site_of(url), source_id=sku or urlparse(url).path or url, url=url, fetched_at=page.fetched_at,
        title=_text(product.get("name")), brand=_text(product.get("brand") or product.get("manufacturer")),
        model=_text(product.get("mpn") or product.get("model")), gtin=gtin_of(product) or gtin_of(offer),
        price=_number(offer.get("price")), currency=_text(offer.get("priceCurrency")),
        rating=rating, review_count=count, in_stock=_stock(offer.get("availability")),
        specs=_specs(product), description=description, reviews=_reviews(product),
        images=_images(product, page.final_url),
        text="\n".join(t for t in (description, text) if t),
    )
    if rec.price is not None and rec.currency is None and rec.source.endswith(".se"):
        rec.currency = "SEK"
    from_data = [f for f in ("title", "brand", "model", "gtin", "price", "in_stock", "rating") if getattr(rec, f)
                 is not None]
    return rec, from_data
