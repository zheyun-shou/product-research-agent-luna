from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from scout.db.store import log_fetch, page_texts, save_record, stored_attributes
from scout.sources.amazon_se.product import parse_product_page

pytestmark = pytest.mark.db
FIXTURES = Path(__file__).parent / "fixtures" / "amazon_se"
NOW = datetime(2026, 10, 3, 10, 15, tzinfo=timezone.utc)


def record(name="product_full.html", asin="B0AAAAAAA1", when=NOW):
    html = (FIXTURES / name).read_text(encoding="utf-8")
    return parse_product_page(html, f"https://www.amazon.se/dp/{asin}", when)


def count(db, table):
    return db.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]


def test_new_record_creates_every_row(db):
    ids = save_record(db, record(), "fridge")
    product = db.execute("SELECT * FROM products WHERE id = %s", (ids.product_id,)).fetchone()
    variant = db.execute("SELECT * FROM variants WHERE id = %s", (ids.variant_id,)).fetchone()
    assert (product["brand"], product["model"], product["category"]) == ("Samsung", "RB38C7B5D22/EF", "fridge")
    assert (variant["price"], variant["currency"], variant["in_stock"]) == (Decimal("8990.00"), "SEK", True)
    assert count(db, "price_history") == 1


def test_saving_the_same_asin_again_updates_and_adds_history(db):
    first = save_record(db, record(), "fridge")
    later = record(when=NOW + timedelta(days=1)).model_copy(update={"price": Decimal("7990.00")})
    second = save_record(db, later, "fridge")
    assert first == second
    assert count(db, "listings") == 1
    assert count(db, "price_history") == 2
    price = db.execute("SELECT price FROM variants WHERE id = %s", (second.variant_id,)).fetchone()["price"]
    assert price == Decimal("7990.00")


def test_same_brand_and_model_on_another_asin_shares_the_product(db):
    a = save_record(db, record(asin="B0AAAAAAA1"), "fridge")
    b = save_record(db, record(asin="B0AAAAAAA9"), "fridge")
    assert a.product_id == b.product_id
    assert a.listing_id != b.listing_id


def test_records_without_a_model_get_their_own_products(db):
    a = save_record(db, record("product_parts_price.html", "B0AAAAAAA3"), "fridge")
    b = save_record(db, record("product_parts_price.html", "B0AAAAAAA4"), "fridge")
    assert a.product_id != b.product_id


def test_unavailable_product_is_stored_out_of_stock_with_history(db):
    ids = save_record(db, record("product_unavailable.html", "B0AAAAAAA2"), "fridge")
    row = db.execute("SELECT price, in_stock FROM price_history WHERE variant_id = %s", (ids.variant_id,)).fetchone()
    assert (row["price"], row["in_stock"]) == (None, False)


def test_attributes_are_merged(db):
    rec = record().model_copy(update={"attributes": {"height": {"value": 203.0, "unit": "cm", "raw": "203 cm"}}})
    ids = save_record(db, rec, "fridge")
    more = record().model_copy(update={"attributes": {"noise": {"value": 35.0, "unit": "dB", "raw": "35 dB"}}})
    save_record(db, more, "fridge")
    attrs = db.execute("SELECT attributes FROM products WHERE id = %s", (ids.product_id,)).fetchone()["attributes"]
    assert set(attrs) == {"height", "noise"}


def test_log_fetch(db):
    log_fetch(db, "amazon_se", "https://www.amazon.se/dp/B0AAAAAAA1", "captcha", 503, 1200, "amazon.se shows a captcha")
    row = db.execute("SELECT * FROM fetch_log").fetchone()
    assert (row["status"], row["http_status"], row["duration_ms"]) == ("captcha", 503, 1200)


def test_generic_brand_does_not_join_products(db):
    a = record(asin="B0AAAAAAA1").model_copy(update={"brand": "Generic", "model": "1"})
    b = record(asin="B0AAAAAAA8").model_copy(update={"brand": "generic", "model": "1"})
    assert save_record(db, a, "incense").product_id != save_record(db, b, "incense").product_id


def test_stored_attributes(db):
    rec = record().model_copy(update={"attributes": {"height": {"value": 203.0, "unit": "cm", "raw": "203 cm"}}})
    save_record(db, rec, "fridge")
    assert stored_attributes(db, "amazon_se", "B0AAAAAAA1")["height"]["value"] == 203.0
    assert stored_attributes(db, "amazon_se", "B0NOTSTORED") == {}


from scout.db.store import add_eliminations, eliminated_products, known_source_ids, restore_products
from tests.test_picks_db import save


def test_rejected_products_belong_to_one_thread_and_can_come_back(db):
    save(db, "product_full.html", "B0AAAAAAA1")
    product_id = db.execute("SELECT id FROM products").fetchone()["id"]
    add_eliminations(db, "t1", [product_id], "too loud")
    add_eliminations(db, "t2", [product_id], "too big")
    assert eliminated_products(db, "t1") == {product_id}
    assert eliminated_products(db, "t3") == set()
    restore_products(db, "t1", [product_id])
    assert eliminated_products(db, "t1") == set() and eliminated_products(db, "t2") == {product_id}


def test_known_source_ids_finds_stored_listings(db):
    save(db, "product_full.html", "B0AAAAAAA1")
    assert known_source_ids(db, "amazon_se", ["B0AAAAAAA1", "B0AAAAAAA9"]) == {"B0AAAAAAA1"}


def test_the_page_text_of_the_newest_page_is_kept(db):
    ids = save_record(db, record(), "fridge")
    texts = page_texts(db, [ids.listing_id])[ids.listing_id]
    assert texts["reviews"][0]["title"] == "Bra men högljudd" and texts["bullets"]
    older = record(when=NOW - timedelta(days=1)).model_copy(update={"description": "old"})
    save_record(db, older, "fridge")
    assert page_texts(db, [ids.listing_id])[ids.listing_id]["description"] != "old"


def test_the_newest_page_makes_the_chunks(db):
    ids = save_record(db, record(), "fridge")
    rows = db.execute("SELECT kind, ref, text, embedding FROM chunks WHERE listing_id = %s ORDER BY id",
                      (ids.listing_id,)).fetchall()
    assert {r["kind"] for r in rows} >= {"specs", "review"} and all(r["embedding"] is None for r in rows)
    assert any(r["ref"] == "review 1" and "Bra men högljudd" in r["text"] for r in rows)
    save_record(db, record(when=NOW - timedelta(days=1)).model_copy(update={"description": "old"}), "fridge")
    again = db.execute("SELECT count(*) AS n FROM chunks WHERE text LIKE '%%old%%'").fetchone()["n"]
    assert again == 0 and count(db, "chunks") == len(rows)
