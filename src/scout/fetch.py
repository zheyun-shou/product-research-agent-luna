"""Our own page fetcher for the pages of other shops and of web search (decided 2026-10-07).

A plain HTTP request comes first: it is fast, and pages of different sites can load in parallel. Pages of one site
load one at a time, with a gap between them. When a site refuses the request, or the page needs JavaScript, the
dedicated Chrome profile opens it. A captcha or a sign-in page stops the read: the program never bypasses it.
"""
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import urlparse

import httpx

from scout.sources.base import RawPage

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/141.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "sv-SE,sv;q=0.9,en;q=0.8",
}
SITE_GAP = 3.0  # seconds between two pages of one site
TIMEOUT = 20.0
GONE = "the page is gone (status 404 or 410)"
MIN_TEXT = 400  # characters of visible text: a page with less needs JavaScript or was refused

# Words of challenge and captcha pages (Cloudflare, DataDome, reCAPTCHA, hCaptcha and others)
CHALLENGE = re.compile(r"cf-chl|challenge-platform|just a moment\.\.\.|captcha-delivery|g-recaptcha|h-captcha|"
                       r"hcaptcha\.com|/captcha|verify you are (a )?human|är du en människa", re.IGNORECASE)
REFUSED = {401, 403, 405, 429, 503}


def site_of(url: str) -> str:
    host = urlparse(url).netloc.lower().split("@")[-1].split(":")[0]
    return host[4:] if host.startswith("www.") else host


def visible_text(html: str) -> str:
    """The text that a reader sees, roughly: no scripts, no styles, no tags."""
    html = re.sub(r"<(script|style|noscript|template)\b.*?</\1\s*>", " ", html, flags=re.IGNORECASE | re.DOTALL)
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def challenge(page: RawPage) -> bool:
    """A captcha or a bot check. Many normal pages load a captcha script for their forms, so the words count only
    on a refused or a nearly empty page."""
    if not CHALLENGE.search(page.html):
        return False
    return page.status in REFUSED or len(visible_text(page.html)) < 2_000


@dataclass
class Fetched:
    page: RawPage | None
    problem: str = ""  # empty when the page loaded
    via: str = "http"  # http or browser


class PageFetcher:
    """`browser(url)` opens a page in the Chrome profile; None means plain HTTP only (for example in a worker
    thread, where the browser cannot run)."""

    def __init__(self, browser: Callable[[str], RawPage] | None = None, client: httpx.Client | None = None,
                 gap: float = SITE_GAP, sleep=time.sleep, clock=time.monotonic):
        self.browser = browser
        self.client = client or httpx.Client(headers=HEADERS, timeout=TIMEOUT, follow_redirects=True)
        self.gap, self._sleep, self._clock = gap, sleep, clock
        self._locks: dict[str, threading.Lock] = {}
        self._last: dict[str, float] = {}
        self._guard = threading.Lock()

    def _site_lock(self, site: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(site, threading.Lock())

    def _wait(self, site: str) -> None:
        last = self._last.get(site)
        if last is not None:
            remaining = self.gap - (self._clock() - last)
            if remaining > 0:
                self._sleep(remaining)

    def _http(self, url: str) -> RawPage | None:
        started = self._clock()
        try:
            response = self.client.get(url)
        except httpx.HTTPError:
            return None
        kind = response.headers.get("content-type", "")
        html = response.text if "html" in kind or "xml" in kind or not kind else ""
        return RawPage(url=url, final_url=str(response.url), status=response.status_code, html=html,
                       fetched_at=datetime.now(timezone.utc), duration_ms=int((self._clock() - started) * 1000))

    def fetch(self, url: str, use_browser: bool = True) -> Fetched:
        site = site_of(url)
        with self._site_lock(site):  # one page of a site at a time
            self._wait(site)
            try:
                page = self._http(url)
            finally:
                self._last[site] = self._clock()
            problem = self.problem(page)
            # Shops that build the page with JavaScript give only the page data (Power, 2026-10-07), without the sizes
            weak = page is not None and not problem and len(visible_text(page.html)) < MIN_TEXT
            if (problem and problem != GONE) or weak:
                if use_browser and self.browser is not None:
                    self._wait(site)
                    try:
                        page = self.browser(url)
                    finally:
                        self._last[site] = self._clock()
                    problem = self.problem(page)
                    return Fetched(None if problem else page, problem, "browser")
                if weak:
                    problem = "the page has almost no text without JavaScript"
            return Fetched(None if problem else page, problem)

    @staticmethod
    def problem(page: RawPage | None) -> str:
        if page is None or page.status == 0:
            return "the page did not answer"
        if challenge(page):
            return "the site shows a captcha or a bot check, so I did not read the page"
        if re.search(r"/(login|signin|sign-in|logga-in)\b", urlparse(page.final_url).path, re.IGNORECASE):
            return "the site asks for a login"
        if page.status in (404, 410):
            return GONE
        if page.status >= 400:
            return f"the site answered with status {page.status}"
        if not page.html:
            return "the address is not a web page"
        return ""

    def close(self) -> None:
        self.client.close()
