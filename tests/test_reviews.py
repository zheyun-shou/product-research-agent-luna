from datetime import timedelta
from pathlib import Path

import pytest

from scout.crawl import crawl
from scout.reviews import page_plan, read_reviews
from scout.sources.amazon_se.adapter import AmazonSeAdapter
from scout.sources.amazon_se.urls import product_url, reviews_url, search_url
from tests.fakes import NOW, FakeBrowser

FIXTURES = Path(__file__).parent / "fixtures" / "amazon_se"
CAPTCHA = '<form action="/errors/validateCaptcha"></form>'


def html(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


CRITICAL = html("reviews_critical.html")
POSITIVE = (CRITICAL.replace("R1AAAAAAAAAAAA", "R9PPPPPPPPPPPP").replace("R2BBBBBBBBBBBB", "R8QQQQQQQQQQQQ")
            .replace("3.0 out of 5", "5.0 out of 5").replace("1,0 av 5", "4,0 av 5")
            .replace("Dörren var böjd vid leverans.", "Tyst och kall."))
RECENT = CRITICAL.replace("R2BBBBBBBBBBBB", "R7NNNNNNNNNNNN").replace("Dörren var böjd", "Kompressorn gick sönder")


def test_page_plan():
    assert page_plan({}, []) == ("positive", "top")
    assert page_plan({}, [("positive", "top")]) == ("critical", "top")
    done = [("positive", "top"), ("critical", "top")]
    assert page_plan({"critical": True}, done) == ("critical", "recent")
    assert page_plan({"critical": False, "positive": True}, done) is None
    assert page_plan({"critical": True}, [*done, ("critical", "recent")]) is None




def never_called(status, url):
    raise AssertionError(f"unexpected problem {status.problem} at {url}")


def stored_listing(db, tmp_path, extra):
    browser = FakeBrowser({search_url("kylskåp"): [html("search_basic.html")],
                           product_url("B0AAAAAAA1"): [html("product_full.html")],
                           product_url("B0AAAAAAA2"): [html("product_unavailable.html")],
                           product_url("B0AAAAAAA3"): [html("product_parts_price.html")], **extra})
    adapter = AmazonSeAdapter(browser, tmp_path)
    crawl(db, adapter, "fridge", "kylskåp", on_problem=never_called)
    listing = db.execute("SELECT id FROM listings WHERE source_id = 'B0AAAAAAA1'").fetchone()["id"]
    return adapter, browser, listing


def review_pages(asin="B0AAAAAAA1", critical=None):
    return {reviews_url(asin, "positive"): [POSITIVE], reviews_url(asin, "critical"): critical or [CRITICAL],
            reviews_url(asin, "critical", "recent"): [RECENT]}


@pytest.mark.db
def test_reads_three_pages_and_stores_reviews_with_their_filter(db, tmp_path):
    adapter, browser, listing = stored_listing(db, tmp_path, review_pages())
    report = read_reviews(db, lambda: adapter, [(listing, "B0AAAAAAA1")], never_called, now=NOW)
    assert (report.listings, report.pages, report.new) == (1, 3, 5)
    assert report.reviews == {"positive": 2, "critical": 3}
    rows = db.execute("SELECT review_id, filter FROM shop_reviews ORDER BY review_id").fetchall()
    assert {r["review_id"]: r["filter"] for r in rows}["R7NNNNNNNNNNNN"] == "critical"
    chunks = db.execute("SELECT ref, text, meta FROM chunks WHERE source = 'review_page' ORDER BY ref").fetchall()
    assert len(chunks) == 5
    assert all(c["text"].startswith("Product: Samsung") for c in chunks)
    assert {c["meta"]["filter"] for c in chunks} == {"positive", "critical"}
    assert "Person" not in str([dict(c) for c in chunks])
    assert "5 of them new" in report.text()


@pytest.mark.db
def test_fresh_reviews_are_not_read_again(db, tmp_path):
    adapter, browser, listing = stored_listing(db, tmp_path, review_pages())
    read_reviews(db, lambda: adapter, [(listing, "B0AAAAAAA1")], never_called, now=NOW)
    pages = len(browser.fetched)
    report = read_reviews(db, lambda: adapter, [(listing, "B0AAAAAAA1")], never_called, now=NOW + timedelta(days=1))
    assert (report.fresh, report.pages, len(browser.fetched)) == (1, 0, pages)
    assert report.text() == ""
    later = read_reviews(db, lambda: adapter, [(listing, "B0AAAAAAA1")], never_called, now=NOW + timedelta(days=8))
    assert (later.pages, later.new) == (3, 0)


@pytest.mark.db
def test_page_limit_stops_the_step(db, tmp_path):
    adapter, browser, listing = stored_listing(db, tmp_path, review_pages())
    report = read_reviews(db, lambda: adapter, [(listing, "B0AAAAAAA1")], never_called, max_pages=2, now=NOW)
    assert report.pages == 2
    assert "limit of 2 review pages" in report.stopped


@pytest.mark.db
def test_skipped_captcha_stops_the_step(db, tmp_path):
    adapter, browser, listing = stored_listing(db, tmp_path, review_pages(critical=[CAPTCHA]))
    asked = []
    report = read_reviews(db, lambda: adapter, [(listing, "B0AAAAAAA1"), (listing + 1000, "B0AAAAAAA9")],
                          lambda status, url: asked.append(status.problem) or False, now=NOW)
    assert asked == ["captcha"]
    assert (report.listings, report.pages, report.reviews["positive"]) == (1, 1, 2)
    assert "skipped" in report.stopped
    assert not any("B0AAAAAAA9" in url for url in browser.fetched)


@pytest.mark.db
def test_a_review_on_the_product_page_gets_no_second_chunk(db, tmp_path):
    adapter, browser, listing = stored_listing(db, tmp_path, review_pages())
    db.execute("""UPDATE page_texts SET reviews = '[{"text": "Tyst  och kall."}]'::jsonb WHERE listing_id = %s""",
               (listing,))
    db.commit()
    read_reviews(db, lambda: adapter, [(listing, "B0AAAAAAA1")], never_called, now=NOW)
    texts = [r["text"] for r in db.execute("SELECT text FROM chunks WHERE source = 'review_page'").fetchall()]
    assert not any("Tyst och kall" in t for t in texts)
    assert db.execute("SELECT count(*) AS n FROM shop_reviews").fetchone()["n"] == 5
