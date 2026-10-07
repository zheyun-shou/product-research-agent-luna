import re
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from scout.db.store import page_texts, save_record
from scout.fetch import Fetched
from scout.shops import read_page, refetch
from scout.sources.base import ProductRecord, RawPage

pytestmark = pytest.mark.db
HTML = (Path(__file__).parent / "fixtures" / "shops" / "product.html").read_text(encoding="utf-8")
URL = "https://www.testbutik.se/product/123456"
NOW = datetime(2026, 10, 7, tzinfo=timezone.utc)


def loaded(html=HTML, url=URL):
    return lambda u: Fetched(RawPage(url=u, final_url=url, status=200, html=html, fetched_at=NOW))


def amazon(brand="Gorenje", model="RK4182PS4", reviews=()):
    return ProductRecord(source="amazon_se", source_id="B0GORENJE1", url="https://www.amazon.se/dp/B0GORENJE1",
                         fetched_at=NOW, title="Gorenje RK4182PS4 kyl/frys", brand=brand, model=model,
                         price=Decimal("6490"), currency="SEK", in_stock=True, reviews=list(reviews))


def test_a_shop_page_is_stored_with_its_reviews(db):
    got = read_page(db, loaded(), URL, "fridge", None)
    assert got.pages == 1 and got.listing_id is not None
    row = db.execute("SELECT l.source, l.source_id, p.gtin, v.price, v.in_stock FROM listings l JOIN products p "
                     "ON p.id = l.product_id JOIN variants v ON v.listing_id = l.id").fetchone()
    assert dict(row) == {"source": "testbutik.se", "source_id": "123456", "gtin": "03838782123456",
                         "price": Decimal("5990.00"), "in_stock": True}
    assert len(page_texts(db, [got.listing_id])[got.listing_id]["reviews"]) == 2
    assert db.execute("SELECT complete FROM review_reads WHERE listing_id = %s", (got.listing_id,)).fetchone()[
        "complete"] is True
    assert got.text.startswith(f"id {got.listing_id}: Gorenje kyl/frys RK 4182 PS4 (testbutik.se) | price=5990.00 SEK")
    assert "2 buyer reviews from this page stored." in got.text


def test_the_same_model_joins_the_amazon_product_and_copied_reviews_are_left_out(db):
    first = save_record(db, amazon(reviews=[{"text": "Mycket  tyst och rymlig."}]), "fridge")
    got = read_page(db, loaded(), URL, "fridge", None)
    product = db.execute("SELECT product_id FROM listings WHERE id = %s", (got.listing_id,)).fetchone()
    assert product["product_id"] == first.product_id
    assert f"The same product as id {first.listing_id} (amazon_se)." in got.text
    assert "1 buyer reviews from this page stored; 1 were left out" in got.text
    # The same page again keeps its own review: only the copy of the amazon.se review is left out
    again = read_page(db, loaded(), URL, "fridge", None)
    assert again.listing_id == got.listing_id and "1 buyer reviews from this page stored; 1 were left out" in again.text
    assert [r["text"] for r in page_texts(db, [got.listing_id])[got.listing_id]["reviews"]] == ["Dörren slår i väggen."]


def test_the_gtin_matches_before_the_model(db):
    other = amazon(brand="Gorenje", model="SOMETHING-ELSE").model_copy(update={"gtin": "03838782123456"})
    first = save_record(db, other, "fridge")
    got = read_page(db, loaded(), URL, "fridge", None)
    assert db.execute("SELECT product_id FROM listings WHERE id = %s", (got.listing_id,)).fetchone()[
        "product_id"] == first.product_id


def test_a_problem_is_reported_and_logged(db):
    got = read_page(db, lambda u: Fetched(None, "the site shows a captcha or a bot check, so I did not read the page"),
                    URL, "fridge", None)
    assert got.listing_id is None and got.text == f"{URL}: not read, the site shows a captcha or a bot check, so " \
                                                  "I did not read the page."
    assert db.execute("SELECT status, note FROM fetch_log").fetchone()["status"] == "failed"
    assert db.execute("SELECT count(*) AS n FROM listings").fetchone()["n"] == 0


def test_a_page_without_a_product(db):
    got = read_page(db, loaded("<html><body><p>Om oss</p></body></html>"), URL, "fridge", None)
    assert got.listing_id is None and "shows no product with a price" in got.text


def test_amazon_pages_go_through_search_shop(db):
    got = read_page(db, loaded(), "https://www.amazon.se/dp/B0X", "fridge", None)
    assert got == got.__class__("amazon.se pages come through search_shop.", 0)


def test_the_worker_fills_what_the_page_data_misses(db):
    html = HTML.replace('"price": "5990.00", ', "")
    seen = []

    def enrich(rec):
        seen.append(rec.price)
        return rec.model_copy(update={"price": Decimal("5990"), "llm_fields": ["price"]}), True
    got = read_page(db, loaded(html), URL, "fridge", enrich)
    assert seen == [None] and "From the page text through the worker: price." in got.text


def test_the_live_check_reads_the_page_again(db):
    read_page(db, loaded(), URL, "fridge", None)
    sold_out = HTML.replace("InStock", "OutOfStock")
    rec = refetch(db, loaded(sold_out), URL, "fridge", None)
    assert rec.in_stock is False
    assert db.execute("SELECT count(*) AS n FROM price_history").fetchone()["n"] == 2


def test_a_link_that_leads_to_amazon_is_not_stored(db):
    got = read_page(db, loaded(url="https://www.amazon.se/dp/B0X"), "https://amzn.to/abc", "fridge", None)
    assert got.listing_id is None and "leads to amazon.se" in got.text


def test_product_data_without_an_offer_is_not_a_shop_page(db):
    html = re.sub(r'"offers": \{[^}]*\},', "", HTML)
    assert '"offers"' not in html
    got = read_page(db, loaded(html), URL, "fridge", None)
    assert got.listing_id is None and "no product with a price" in got.text


def test_the_live_check_calls_the_worker_only_for_a_missing_price_or_stock(db):
    calls = []
    enrich = lambda rec: (calls.append(rec.source_id) or rec, True)  # noqa: E731
    refetch(db, loaded(), URL, "fridge", enrich)
    assert calls == []


def test_a_model_written_in_another_way_is_listed_and_luna_can_join_it(db):
    from scout.db.store import join_products
    first = save_record(db, amazon(model="732025", reviews=[{"text": "Mycket tyst och rymlig."}]), "fridge")
    got = read_page(db, loaded(), URL, "fridge", None)
    assert f"Stored products of the same brand in other shops: id {first.listing_id} Gorenje RK4182PS4 kyl/frys " \
           "(amazon_se)." in got.text
    db.execute("INSERT INTO eliminations (thread_id, product_id, removed_by) SELECT 't', product_id, 'user' "
               "FROM listings WHERE id = %s", (got.listing_id,))
    assert join_products(db, got.listing_id, first.listing_id) is None
    rows = db.execute("SELECT product_id FROM listings ORDER BY id").fetchall()
    assert {r["product_id"] for r in rows} == {first.product_id}
    product = db.execute("SELECT gtin, model FROM products WHERE id = %s", (first.product_id,)).fetchone()
    assert dict(product) == {"gtin": "03838782123456", "model": "732025"}
    assert db.execute("SELECT product_id FROM eliminations").fetchone()["product_id"] == first.product_id
    # The review that the amazon.se listing has already is left out of the shop page
    assert [r["text"] for r in page_texts(db, [got.listing_id])[got.listing_id]["reviews"]] == ["Dörren slår i väggen."]
    assert join_products(db, got.listing_id, 999999) == "there is no product with id 999999"


def test_listings_of_one_shop_or_of_two_categories_are_not_joined(db):
    from scout.db.store import join_products
    one = save_record(db, amazon(model="A1"), "fridge")
    two = save_record(db, amazon(model="A2").model_copy(update={"source_id": "B0GORENJE2",
                                                                "url": "https://www.amazon.se/dp/B0GORENJE2"}), "fridge")
    assert join_products(db, two.listing_id, one.listing_id).startswith("both are listings of amazon_se")
    got = read_page(db, loaded(), URL, "incense", None)
    assert join_products(db, got.listing_id, one.listing_id) == "they are in different categories"
    assert db.execute("SELECT count(*) AS n FROM products").fetchone()["n"] == 3
