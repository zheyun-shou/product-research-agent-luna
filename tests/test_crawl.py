from decimal import Decimal
from pathlib import Path

import pytest

from scout.crawl import crawl
from scout.sources.amazon_se.adapter import AmazonSeAdapter
from scout.sources.amazon_se.urls import product_url, search_url
from tests.fakes import FakeBrowser

pytestmark = pytest.mark.db
FIXTURES = Path(__file__).parent / "fixtures" / "amazon_se"
CAPTCHA = '<form action="/errors/validateCaptcha"></form>'


def html(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def site(captcha_first=False):
    first = [CAPTCHA, html("product_full.html")] if captcha_first else [html("product_full.html")]
    return FakeBrowser({
        search_url("kylskåp"): [html("search_basic.html")],
        product_url("B0AAAAAAA1"): first,
        product_url("B0AAAAAAA2"): [html("product_unavailable.html")],
        product_url("B0AAAAAAA3"): [html("product_parts_price.html")],
    })


def never_called(status, url):
    raise AssertionError(f"unexpected problem {status.problem} at {url}")


def count(db, table):
    return db.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]


def test_crawl_saves_every_candidate(db, tmp_path):
    adapter = AmazonSeAdapter(site(), tmp_path)
    report = crawl(db, adapter, "fridge", "kylskåp", on_problem=never_called)
    assert (report.search_pages, report.candidates, report.saved) == (1, 3, 3)
    assert count(db, "listings") == 3
    assert count(db, "fetch_log") == 4
    assert report.missing["price"] == 1
    assert any(tmp_path.glob("amazon_se/product/B0AAAAAAA1/*.html"))


def test_budget_drops_known_prices_above_it(db, tmp_path):
    report = crawl(db, AmazonSeAdapter(site(), tmp_path), "fridge", "kylskåp",
                   on_problem=never_called, budget=Decimal("7000"))
    assert (report.over_budget, report.saved) == (1, 2)


def test_captcha_pauses_then_retries_the_same_url(db, tmp_path):
    calls = []

    def solved(status, url):
        calls.append((status.problem, url))
        return True

    browser = site(captcha_first=True)
    report = crawl(db, AmazonSeAdapter(browser, tmp_path), "fridge", "kylskåp", on_problem=solved)
    assert calls == [("captcha", product_url("B0AAAAAAA1"))]
    assert browser.fetched.count(product_url("B0AAAAAAA1")) == 2
    assert report.saved == 3


def test_skipping_a_captcha_moves_on(db, tmp_path):
    report = crawl(db, AmazonSeAdapter(site(captcha_first=True), tmp_path), "fridge", "kylskåp",
                   on_problem=lambda status, url: False)
    assert report.saved == 2
    assert report.skipped == [product_url("B0AAAAAAA1")]


def test_max_details_caps_detail_pages(db, tmp_path):
    report = crawl(db, AmazonSeAdapter(site(), tmp_path), "fridge", "kylskåp",
                   on_problem=never_called, max_details=1)
    assert (report.detail_pages, report.saved) == (1, 1)


def test_enricher_is_called_and_counted(db, tmp_path):
    def enrich(rec):
        return rec.model_copy(update={"llm_fields": ["model"]}), True

    report = crawl(db, AmazonSeAdapter(site(), tmp_path), "fridge", "kylskåp",
                   on_problem=never_called, enrich=enrich)
    assert report.llm_pages == 3


def test_adapter_without_terms_check_is_refused(db, tmp_path):
    adapter = AmazonSeAdapter(site(), tmp_path)
    adapter.terms_check = ""
    with pytest.raises(RuntimeError):
        crawl(db, adapter, "fridge", "kylskåp", on_problem=never_called)


def test_last_attempt_does_not_ask_and_says_why(db, tmp_path):
    calls = []

    def solved(status, url):
        calls.append(url)
        return True

    browser = site()
    browser.pages[product_url("B0AAAAAAA1")] = [CAPTCHA]
    report = crawl(db, AmazonSeAdapter(browser, tmp_path), "fridge", "kylskåp", on_problem=solved)
    assert browser.fetched.count(product_url("B0AAAAAAA1")) == 3
    assert len(calls) == 2
    assert report.skipped == [product_url("B0AAAAAAA1")]
    assert any("gave up after 3 attempts (captcha)" in note for note in report.notes)


def test_failed_search_page_is_reported(db, tmp_path):
    browser = site()
    browser.pages[search_url("kylskåp")] = [CAPTCHA]
    report = crawl(db, AmazonSeAdapter(browser, tmp_path), "fridge", "kylskåp",
                   on_problem=lambda status, url: False)
    assert (report.search_pages, report.saved) == (0, 0)
    assert any("search stopped at page 1" in note for note in report.notes)
