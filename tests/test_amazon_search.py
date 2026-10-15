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


def test_rating_link_to_reviews_is_not_the_count():
    html = """
    <div data-component-type="s-search-result" data-asin="B0AAAAAAA7">
      <h2><span>Rökelse sandelträ</span></h2>
      <a href="/dp/B0AAAAAAA7#customerReviews"><span class="a-icon-alt">4,5 av 5 stjärnor</span></a>
      <a href="/dp/B0AAAAAAA7#customerReviews"><span>1&nbsp;234</span></a>
    </div>"""
    card = parse_search_page(html)[0]
    assert (card.rating, card.review_count) == (Decimal("4.5"), 1234)


def test_filters_of_the_page_with_the_ranges_of_number_options():
    from scout.sources.amazon_se.search import parse_filters
    groups = {g.name: g for g in parse_filters((FIXTURES / "search_filters.html").read_text(encoding="utf-8"))}
    # Delivery and price are not product filters; the price has its own range parameter
    assert list(groups) == ["Customer Review", "Colour", "Width", "Installation"]
    assert [(o.name, o.value) for o in groups["Colour"].options] == [
        ("Red", "p_n_size_two_browse-vebin:20693852031"), ("Black", "p_n_size_two_browse-vebin:20693837031")]
    width = groups["Width"]
    assert width.numeric and not groups["Colour"].numeric
    assert [(o.low, o.high, o.unit) for o in width.options] == [(None, 47.9, "cm"), (48.0, 60.9, "cm"),
                                                                (74.0, None, "cm")]
    # The name comes from the text when the link has no label; an option without a link is left out
    assert [o.name for o in groups["Installation"].options] == ["Undercounter"]
