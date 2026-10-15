from scout.sources.amazon_se.urls import (
    asin_from_reviews_url, asin_from_url, page_kind, product_url, reviews_url, search_url, snapshot_key,
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


def test_reviews_url():
    assert reviews_url("B0AAAAAAA1", "positive") == ("https://www.amazon.se/product-reviews/B0AAAAAAA1/"
                                                      "?filterByStar=positive&reviewerType=all_reviews&pageNumber=1")
    url = reviews_url("B0AAAAAAA1", "critical", "recent")
    assert url.endswith("?filterByStar=critical&reviewerType=all_reviews&pageNumber=1&sortBy=recent")
    assert asin_from_reviews_url(url) == "B0AAAAAAA1"
    assert asin_from_url(url) is None
    assert page_kind(url) == "reviews"
    assert snapshot_key(url) == "B0AAAAAAA1-critical-recent"


def test_reviews_url_needs_a_known_filter():
    import pytest
    with pytest.raises(ValueError):
        reviews_url("B0AAAAAAA1", "three_star")
    with pytest.raises(ValueError):
        reviews_url("B0AAAAAAA1", "critical", "helpful")


def test_search_url_with_a_sort():
    url = search_url("kylskåp", 1, "sales")
    assert url == "https://www.amazon.se/s?k=kylsk%C3%A5p&s=exact-aware-popularity-rank"
    assert snapshot_key(url) == "kylskåp-p1-exact-aware-popularity-rank"


def test_search_url_with_filters_and_its_snapshot_key():
    from decimal import Decimal

    from scout.sources.amazon_se.urls import price_filter, refinements
    assert price_filter(Decimal("6500")) == ["p_36:-650000"] and price_filter(None) == []
    assert refinements(["h:1", "w:7", "h:2", "h:1"]) == "h:1|2,w:7"
    url = search_url("kylskåp", 2, "rating", ["p_36:-650000", "c:20693852031"])
    assert url == ("https://www.amazon.se/s?k=kylsk%C3%A5p&rh=p_36%3A-650000%2Cc%3A20693852031&page=2"
                   "&s=review-rank")
    key = snapshot_key(url)
    assert key.startswith("kylskåp-p2-review-rank-f") and key != snapshot_key(search_url("kylskåp", 2, "rating"))
