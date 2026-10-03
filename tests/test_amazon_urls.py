from scout.sources.amazon_se.urls import (
    asin_from_url, page_kind, product_url, search_url, snapshot_key,
)


def test_search_url():
    assert search_url("kylskåp") == "https://www.amazon.se/s?k=kylsk%C3%A5p"
    assert search_url("kylskåp", 2) == "https://www.amazon.se/s?k=kylsk%C3%A5p&page=2"


def test_product_url_and_asin():
    assert product_url("B0AAAAAAA1") == "https://www.amazon.se/dp/B0AAAAAAA1"
    assert asin_from_url("https://www.amazon.se/Samsung-RB38/dp/B0AAAAAAA1/ref=sr_1_1?k=x") == "B0AAAAAAA1"
    assert asin_from_url("https://www.amazon.se/s?k=x") is None


def test_snapshot_key():
    assert snapshot_key("https://www.amazon.se/x/dp/B0AAAAAAA1/ref=y") == "B0AAAAAAA1"
    assert snapshot_key(search_url("kylskåp", 2)) == "kylskåp-p2"
    assert snapshot_key("https://www.amazon.se/") == "home-p1"


def test_page_kind():
    assert page_kind(search_url("kylskåp")) == "search"
    assert page_kind(product_url("B0AAAAAAA1")) == "product"
    assert page_kind("https://www.amazon.se/") == "other"
