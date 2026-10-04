from datetime import datetime, timezone
from decimal import Decimal

import pytest

from scout.picks.rank import (
    Card, Condition, apply_conditions, bayesian, drop_implausible_sizes, parse_condition, rank,
)

NOW = datetime(2026, 10, 3, 10, 15, tzinfo=timezone.utc)


def card(listing_id, rating=None, count=None, **attrs):
    return Card(listing_id=listing_id, product_id=listing_id, title=f"Fridge {listing_id}", brand=None, model=None,
                url=f"https://www.amazon.se/dp/B0AAAAAAA{listing_id}", price=Decimal("5000"), currency="SEK",
                in_stock=True, rating=None if rating is None else Decimal(str(rating)), review_count=count,
                attributes={k: {"value": v} for k, v in attrs.items()}, fetched_at=NOW)


def test_parse_condition():
    assert parse_condition("height<=180") == Condition("height", "<=", 180.0)
    assert parse_condition(" total_volume >= 300,5 ") == Condition("total_volume", ">=", 300.5)
    with pytest.raises(ValueError):
        parse_condition("height < 180")


def test_few_perfect_reviews_do_not_beat_many_good_ones():
    assert bayesian(Decimal("4.6"), 3000, 4.2) > bayesian(Decimal("5.0"), 2, 4.2)
    assert bayesian(None, None, 4.2) == 4.2


def test_conditions_drop_failures_and_mark_missing():
    cards = [card(1, height=175), card(2, height=190), card(3)]
    kept, dropped = apply_conditions(cards, [Condition("height", "<=", 180)])
    assert [c.listing_id for c in kept] == [1, 3]
    assert dropped == 1
    assert kept[1].missing == ["height"]


def test_rank_puts_missing_data_last_then_by_score():
    a = card(1, 4.0, 500)
    b = card(2, 4.8, 800)
    c = card(3, 5.0, 900)
    c.missing = ["height"]
    assert [x.listing_id for x in rank([a, b, c])] == [2, 1, 3]


def test_rank_keeps_one_card_per_product():
    # Colours of one fridge are separate listings of the same product.
    a, b, c = card(1, 4.0, 10), card(2, 4.6, 300), card(3, 4.5, 200)
    b.product_id = 1
    assert [x.listing_id for x in rank([a, b, c])] == [2, 3]


def test_sizes_too_small_for_the_volume_are_ignored():
    # A seller wrote "10D x 10B x 10H millimeter" for a 173 l fridge.
    junk = card(1, height=1.0, width=1.0, depth=1.0, total_volume=173.0)
    real = card(2, height=143.0, width=55.0, depth=55.0, total_volume=204.0)
    notes = drop_implausible_sizes([junk, real])
    assert "height" not in junk.attributes and junk.attributes["total_volume"]["value"] == 173.0
    assert real.attributes["height"]["value"] == 143.0
    assert list(notes) == [1] and "173" in notes[1]
