from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from scout.db.store import save_record
from scout.picks.rank import load_cards
from scout.sources.amazon_se.product import parse_product_page

pytestmark = pytest.mark.db
FIXTURES = Path(__file__).parent / "fixtures" / "amazon_se"
NOW = datetime(2026, 10, 3, 10, 15, tzinfo=timezone.utc)


def save(db, name, asin):
    html = (FIXTURES / name).read_text(encoding="utf-8")
    save_record(db, parse_product_page(html, f"https://www.amazon.se/dp/{asin}", NOW), "fridge")


def test_load_cards_skips_out_of_stock_and_applies_the_budget(db):
    save(db, "product_full.html", "B0AAAAAAA1")         # 8990 SEK, in stock
    save(db, "product_unavailable.html", "B0AAAAAAA2")  # out of stock
    save(db, "product_parts_price.html", "B0AAAAAAA3")  # 5499 SEK, in stock
    assert {c.url[-10:] for c in load_cards(db, "fridge", None)} == {"B0AAAAAAA1", "B0AAAAAAA3"}
    assert [c.url[-10:] for c in load_cards(db, "fridge", Decimal("6000"))] == ["B0AAAAAAA3"]


def test_a_card_shows_the_newest_variant_of_its_listing(db):
    from scout.db.store import save_variant
    from scout.picks.rank import load_cards_by_ids
    save(db, "product_full.html", "B0AAAAAAA1")  # 8990 SEK, in stock
    listing_id = db.execute("SELECT id FROM listings WHERE source_id = 'B0AAAAAAA1'").fetchone()["id"]
    size = parse_product_page((FIXTURES / "product_unavailable.html").read_text(encoding="utf-8"),
                              "https://www.amazon.se/dp/B0AAAAAAA9", NOW.replace(hour=11))
    save_variant(db, listing_id, size)  # the page of one size, sold out
    assert load_cards(db, "fridge", None) == []
    assert load_cards_by_ids(db, [listing_id])[0].in_stock is False
