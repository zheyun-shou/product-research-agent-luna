from datetime import datetime, timezone

import httpx

from scout.fetch import GONE, PageFetcher, challenge, site_of
from scout.sources.base import RawPage

TEXT = "<html><body><main>" + "Ett rymligt kylskåp med tyst kompressor. " * 20 + "</main></body></html>"


class Clock:
    def __init__(self):
        self.now, self.slept = 0.0, []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(round(seconds, 2))
        self.now += seconds


def fetcher(handler, browser=None, clock=None):
    clock = clock or Clock()
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    return PageFetcher(browser=browser, client=client, gap=3.0, sleep=clock.sleep, clock=clock)


def browser_page(url, html=TEXT, status=200):
    return RawPage(url=url, final_url=url, status=status, html=html, fetched_at=datetime.now(timezone.utc))


def test_a_plain_request_reads_the_page():
    got = fetcher(lambda r: httpx.Response(200, html=TEXT)).fetch("https://www.shop.se/p/1")
    assert got.problem == "" and got.via == "http" and "kylskåp" in got.page.html


def test_pages_of_one_site_wait_for_the_gap_and_other_sites_do_not():
    clock = Clock()
    f = fetcher(lambda r: httpx.Response(200, html=TEXT), clock=clock)
    f.fetch("https://shop.se/1")
    f.fetch("https://other.se/1")
    f.fetch("https://www.shop.se/2")
    assert clock.slept == [3.0]


def test_a_refused_request_opens_the_browser():
    opened = []
    f = fetcher(lambda r: httpx.Response(403, html="<html>no</html>"),
                browser=lambda url: opened.append(url) or browser_page(url))
    got = f.fetch("https://shop.se/1")
    assert opened == ["https://shop.se/1"] and got.via == "browser" and got.problem == ""


def test_a_page_without_text_opens_the_browser_also_with_product_data():
    opened = []
    browser = lambda url: opened.append(url) or browser_page(url)  # noqa: E731
    fetcher(lambda r: httpx.Response(200, html="<html><div id=app></div></html>"), browser).fetch("https://a.se/1")
    data = '<html><script type="application/ld+json">{"@type": "Product"}</script></html>'
    fetcher(lambda r: httpx.Response(200, html=data), browser).fetch("https://b.se/1")
    fetcher(lambda r: httpx.Response(200, html=TEXT), browser).fetch("https://c.se/1")
    assert opened == ["https://a.se/1", "https://b.se/1"]


def test_a_captcha_stops_the_read():
    captcha = '<html><title>Just a moment...</title><div class="cf-chl-widget"></div></html>'
    got = fetcher(lambda r: httpx.Response(403, html=captcha),
                  browser=lambda url: browser_page(url, captcha, 403)).fetch("https://shop.se/1")
    assert got.page is None and "captcha" in got.problem and got.via == "browser"


def test_a_captcha_script_on_a_normal_page_is_not_a_captcha():
    html = TEXT.replace("</body>", '<script src="https://www.google.com/recaptcha/api.js"></script>'
                                   '<div class="g-recaptcha"></div>' + "<p>Mer text om kylen.</p>" * 80 + "</body>")
    assert not challenge(browser_page("https://shop.se/1", html))


def test_a_missing_page_does_not_open_the_browser():
    opened = []
    got = fetcher(lambda r: httpx.Response(404, html="gone"), browser=opened.append).fetch("https://shop.se/1")
    assert got.problem == GONE and opened == []


def test_without_a_browser_a_refused_page_is_a_problem():
    got = fetcher(lambda r: httpx.Response(429, html="slow down")).fetch("https://shop.se/1", use_browser=False)
    assert got.page is None and "429" in got.problem


def test_site_of():
    assert site_of("https://www.Elgiganten.se/product/1") == "elgiganten.se"
    assert site_of("http://user@shop.de:8080/x") == "shop.de"
