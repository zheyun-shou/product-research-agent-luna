from decimal import Decimal
from pathlib import Path

from scout.sources.amazon_se.search import has_next_page, parse_search_page

FIXTURES = Path(__file__).parent / "fixtures" / "amazon_se"


def cards():
    return parse_search_page((FIXTURES / "search_basic.html").read_text(encoding="utf-8"))


def test_reads_cards_in_page_order_and_skips_bad_ones():
    assert [c.source_id for c in cards()] == ["B0AAAAAAA1", "B0AAAAAAA2", "B0AAAAAAA3"]


def test_first_card_fields():
    c = cards()[0]
    assert c.title == "Samsung RB38C7B5D22 kyl/frys 203 cm"
    assert c.url == "https://www.amazon.se/dp/B0AAAAAAA1"
    assert (c.price, c.currency) == (Decimal("8990.00"), "SEK")
    assert (c.rating, c.review_count) == (Decimal("4.5"), 1234)
    assert c.sponsored is False


def test_struck_through_price_is_not_the_price():
    c = cards()[1]
    assert c.price == Decimal("6499.00")
    assert c.sponsored is True


def test_card_without_price_or_reviews():
    c = cards()[2]
    assert (c.price, c.rating, c.review_count) == (None, None, None)


def test_has_next_page():
    assert has_next_page((FIXTURES / "search_basic.html").read_text(encoding="utf-8"))
    assert not has_next_page(
        '<span class="s-pagination-item s-pagination-next s-pagination-disabled">Nästa</span>'
    )
    assert not has_next_page("<html></html>")
