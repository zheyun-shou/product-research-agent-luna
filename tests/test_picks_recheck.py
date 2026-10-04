from decimal import Decimal

from scout.picks.recheck import recheck_picks
from tests.test_picks_rank import NOW, card
from scout.sources.base import ProductRecord


def fresh(c, price="5000", in_stock=True):
    return ProductRecord(source="amazon_se", source_id=c.url[-10:], url=c.url, fetched_at=NOW,
                         price=Decimal(price), currency="SEK", in_stock=in_stock)


def test_sold_out_and_over_budget_picks_are_replaced():
    ranked = [card(1), card(2), card(3), card(4)]
    states = {1: dict(in_stock=False), 2: dict(price="9000"), 3: {}, 4: {}}
    picks, notes = recheck_picks(ranked, 2, lambda c: fresh(c, **states[c.listing_id]), Decimal("8000"))
    assert [p.listing_id for p in picks] == [3, 4]
    assert len(notes) == 2


def test_recheck_updates_the_price_shown():
    picks, _ = recheck_picks([card(1)], 1, lambda c: fresh(c, price="4500"), None)
    assert picks[0].price == Decimal("4500")


def test_pages_that_cannot_be_rechecked_are_skipped():
    picks, notes = recheck_picks([card(1), card(2)], 1, lambda c: None if c.listing_id == 1 else fresh(c), None)
    assert [p.listing_id for p in picks] == [2]
    assert "could not be rechecked" in notes[0]


def test_sizes_found_on_recheck_must_meet_the_conditions():
    from scout.picks.rank import Condition

    def with_width(c, width):
        rec = fresh(c)
        return rec.model_copy(update={"attributes": {"width": {"value": width, "unit": "cm"}}})

    ranked = [card(1), card(2)]
    picks, notes = recheck_picks(ranked, 2, lambda c: with_width(c, 61.5 if c.listing_id == 1 else 55.0), None,
                                 [Condition("width", "<=", 60)])
    assert [p.listing_id for p in picks] == [2]
    assert picks[0].attributes["width"]["value"] == 55.0 and picks[0].missing == []
    assert "width" in notes[0]
