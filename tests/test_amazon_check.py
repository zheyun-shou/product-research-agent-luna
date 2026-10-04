from datetime import datetime, timezone

from scout.sources.amazon_se.check import check_page
from scout.sources.base import RawPage

NOW = datetime(2026, 10, 3, 10, 15, tzinfo=timezone.utc)
URL = "https://www.amazon.se/dp/B0AAAAAAA1"


def page(html='<html><body><span id="productTitle">ok</span></body></html>', status=200, final_url=URL):
    return RawPage(url=URL, final_url=final_url, status=status, html=html, fetched_at=NOW)


def test_normal_page_is_ok():
    assert check_page(page()).ok


def test_captcha_page():
    status = check_page(page('<form action="/errors/validateCaptcha"></form>', status=503))
    assert (status.ok, status.problem) == (False, "captcha")


def test_sign_in_redirect():
    status = check_page(page(final_url="https://www.amazon.se/ap/signin?openid=x"))
    assert status.problem == "signin"


def test_server_error_and_no_response_are_blocked():
    assert check_page(page(status=503)).problem == "blocked"
    assert check_page(page(status=0, html="")).problem == "blocked"


def test_missing_product_is_gone():
    assert check_page(page(status=404)).problem == "gone"


def test_product_page_without_its_content_is_incomplete():
    status = check_page(page("<html><title>Bomann : Amazon.se</title><body><div id='nav'></div></body></html>"))
    assert status.problem == "incomplete"
    assert check_page(page('<span id="productTitle">Bomann</span>')).ok
