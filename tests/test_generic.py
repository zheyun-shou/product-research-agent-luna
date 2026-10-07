from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from scout.sources.base import RawPage
from scout.sources.generic import gtin_of, json_ld, products_of, read_generic

HTML = (Path(__file__).parent / "fixtures" / "shops" / "product.html").read_text(encoding="utf-8")
NOW = datetime(2026, 10, 7, tzinfo=timezone.utc)


def page(html: str, url: str = "https://www.testbutik.se/product/123456?utm=x") -> RawPage:
    return RawPage(url=url, final_url=url, status=200, html=html, fetched_at=NOW)


def test_the_product_data_of_the_page():
    rec, from_data = read_generic(page(HTML))
    assert rec.source == "testbutik.se" and rec.source_id == "123456"
    assert rec.url == "https://www.testbutik.se/product/vitvaror/kyl-frys/123456/gorenje-rk4182ps4"
    assert (rec.title, rec.brand, rec.model) == ("Gorenje kyl/frys RK 4182 PS4", "Gorenje", "RK 4182 PS4")
    assert rec.gtin == "03838782123456"
    assert (rec.price, rec.currency, rec.in_stock) == (Decimal("5990.00"), "SEK", True)
    assert (rec.rating, rec.review_count) == (Decimal("4.50"), 42)  # 9 of 10
    assert rec.specs == {"Höjd": "180 cm", "Ljudnivå": "35 dB"}
    assert rec.images == ["https://img.testbutik.se/1.jpg", "https://www.testbutik.se/img/2.jpg"]
    assert set(from_data) == {"title", "brand", "model", "gtin", "price", "in_stock", "rating"}


def test_the_reviews_never_keep_the_author():
    rec, _ = read_generic(page(HTML))
    assert rec.reviews[0] == {"title": "Tyst", "rating": 5.0, "date": "2026-08-01", "verified": False,
                              "text": "Mycket tyst och rymlig."}
    assert "Anna" not in str(rec.reviews) and "Bo" not in str(rec.reviews)


def test_the_page_text_has_the_main_text_and_the_table():
    rec, _ = read_generic(page(HTML))
    assert "NoFrost" in rec.text and "180 cm" in rec.text and "60 cm" in rec.text


def test_a_page_without_product_data():
    rec, from_data = read_generic(page("<html><body><p>Hello</p></body></html>", "https://shop.dk/a/b"))
    assert rec.title is None and rec.price is None and from_data == []
    assert rec.source == "shop.dk" and rec.source_id == "/a/b"


def test_a_product_group_gives_its_first_variant():
    html = """<script type="application/ld+json">{"@type": "ProductGroup", "name": "Fridge", "brand": "Bosch",
      "hasVariant": [{"@type": "Product", "sku": "v1", "offers": {"@type": "Offer", "price": 100,
      "priceCurrency": "EUR", "availability": "OutOfStock"}}]}</script>"""
    rec, _ = read_generic(page(html, "https://shop.de/p"))
    assert (rec.title, rec.brand, rec.source_id) == ("Fridge", "Bosch", "v1")
    assert (rec.price, rec.currency, rec.in_stock) == (Decimal("100"), "EUR", False)


def test_an_aggregate_offer_gives_the_low_price():
    html = """<script type="application/ld+json">[{"@type": "Product", "name": "X",
      "offers": {"@type": "AggregateOffer", "lowPrice": "1 299,00", "priceCurrency": "SEK"}}]</script>"""
    rec, _ = read_generic(page(html))
    assert rec.price == Decimal("1299.00")


def test_broken_json_and_line_breaks_in_strings():
    html = ('<script type="application/ld+json">{broken</script>'
            '<script type="application/ld+json">{"@type": "Product", "name": "A\nB"}</script>')
    assert [p["name"] for p in products_of(json_ld(html))] == ["A B"]


def test_gtin_forms():
    assert gtin_of({"gtin": "3838782123456"}) == "03838782123456"
    assert gtin_of({"gtin12": "012345678905"}) == "00012345678905"
    assert gtin_of({"gtin13": "12-34"}) is None
