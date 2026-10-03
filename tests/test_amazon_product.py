from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from scout.sources.amazon_se.product import parse_product_page

FIXTURES = Path(__file__).parent / "fixtures" / "amazon_se"
NOW = datetime(2026, 10, 3, 10, 15, tzinfo=timezone.utc)


def parse(name, url):
    return parse_product_page((FIXTURES / name).read_text(encoding="utf-8"), url, NOW)


def test_full_page():
    rec = parse("product_full.html", "https://www.amazon.se/Samsung/dp/B0AAAAAAA1/ref=sr_1_1?k=x")
    assert rec.source_id == "B0AAAAAAA1"
    assert rec.url == "https://www.amazon.se/dp/B0AAAAAAA1"
    assert rec.title == "Samsung RB38C7B5D22/EF kyl/frys, 203 cm, 390 liter"
    assert (rec.brand, rec.model) == ("Samsung", "RB38C7B5D22/EF")
    assert (rec.price, rec.currency) == (Decimal("8990.00"), "SEK")
    assert (rec.rating, rec.review_count) == (Decimal("4.5"), 1234)
    assert rec.in_stock is True
    assert rec.variant_label == "Svart"
    assert rec.specs["Produktmått"] == "65,8 x 59,5 x 203 cm"
    assert rec.bullets == ["Höjd 203 cm, bredd 59,5 cm", "Ljudnivå 35 dB"]
    assert "Produktmått: 65,8 x 59,5 x 203 cm" in rec.text
    assert "Ljudnivå 35 dB" in rec.text
    assert rec.missing_core() == []


def test_unavailable_page_has_no_price_and_is_out_of_stock():
    rec = parse("product_unavailable.html", "https://www.amazon.se/dp/B0AAAAAAA2")
    assert rec.in_stock is False
    assert rec.price is None
    assert (rec.brand, rec.model) == ("Bosch", "KGN39VLEB")
    assert rec.missing_core() == ["price"]


def test_price_split_into_parts_and_brand_from_byline():
    rec = parse("product_parts_price.html", "https://www.amazon.se/dp/B0AAAAAAA3")
    assert (rec.price, rec.currency) == (Decimal("5499.00"), "SEK")
    assert rec.brand == "Electrolux"
    assert rec.in_stock is True
    assert (rec.rating, rec.review_count, rec.model) == (None, None, None)


def test_brand_from_store_link():
    html = '<span id="productTitle">X</span><a id="bylineInfo">Besök Samsung-butiken</a>'
    assert parse_product_page(html, "https://www.amazon.se/dp/B0AAAAAAA4", NOW).brand == "Samsung"


def test_page_without_asin_is_refused():
    with pytest.raises(ValueError):
        parse_product_page("<html></html>", "https://www.amazon.se/gp/help", NOW)


@pytest.mark.parametrize("text", ["Finns inte i lager.", "Ej i lager"])
def test_negative_stock_phrases_are_out_of_stock(text):
    html = f'<span id="productTitle">X</span><div id="availability"><span>{text}</span></div>'
    assert parse_product_page(html, "https://www.amazon.se/dp/B0AAAAAAA5", NOW).in_stock is False


def test_marketing_name_is_not_the_model():
    html = (
        '<span id="productTitle">X</span><table id="productDetails_techSpec_section_1">'
        "<tr><th>Modellnamn</th><td>Rökelse</td></tr></table>"
    )
    assert parse_product_page(html, "https://www.amazon.se/dp/B0AAAAAAA6", NOW).model is None


def test_details_table_without_id_and_modell_key():
    html = (
        '<span id="productTitle">X</span><table class="a-keyvalue prodDetTable">'
        "<tr><th>Modellnamn</th><td>Kylan</td></tr><tr><th>Modell</th><td>KX 100</td></tr>"
        "<tr><th>Tillverkare</th><td>Exempel AB</td></tr></table>"
    )
    rec = parse_product_page(html, "https://www.amazon.se/dp/B0AAAAAAA7", NOW)
    assert rec.model == "KX 100"
    assert rec.specs["Tillverkare"] == "Exempel AB"


def test_variant_label_from_inline_twister():
    html = (
        '<span id="productTitle">X</span>'
        '<div id="inline-twister-dim-title-color_name"><span class="inline-twister-dim-title-value">Röd</span></div>'
        '<div id="inline-twister-dim-title-size_name"><span class="inline-twister-dim-title-value">40 L</span></div>'
    )
    assert parse_product_page(html, "https://www.amazon.se/dp/B0AAAAAAA8", NOW).variant_label == "Röd / 40 L"


@pytest.mark.parametrize("line, sizes", [
    ("80D x 45B x 48H centimeter", (48.0, 45.0, 80.0)),
    ("650D x 600B x 1850H millimeter", (185.0, 60.0, 65.0)),
])
def test_size_line_gives_height_width_depth_in_cm(line, sizes):
    html = (
        '<span id="productTitle">X</span><table class="a-keyvalue prodDetTable">'
        f"<tr><th>Produktens mått</th><td>{line}</td></tr></table>"
    )
    attrs = parse_product_page(html, "https://www.amazon.se/dp/B0AAAAAAA9", NOW).attributes
    assert tuple(attrs[name]["value"] for name in ("height", "width", "depth")) == sizes
    assert attrs["height"]["raw"] == line
    assert attrs["height"]["unit"] == "cm"


def test_size_line_without_letters_is_not_read():
    # "65,8 x 59,5 x 203 cm" does not say which number is the height.
    assert parse("product_full.html", "https://www.amazon.se/dp/B0AAAAAAA1").attributes == {}
