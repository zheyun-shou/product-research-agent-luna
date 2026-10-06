"""A dedicated Chrome profile driven by Playwright, one page at a time."""
import random
import time
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from scout.sources.base import RawPage


class PoliteTimer:
    """Keeps a random gap of min_delay to max_delay seconds between page loads. About 3 seconds since 2026-10-06."""

    def __init__(self, min_delay=2.5, max_delay=3.5, rng=random.random, sleep=time.sleep, clock=time.monotonic):
        self.min_delay = min_delay
        self.max_delay = max_delay
        self._rng = rng
        self._sleep = sleep
        self._clock = clock
        self._last: float | None = None

    def wait(self) -> None:
        if self._last is not None:
            delay = self.min_delay + (self.max_delay - self.min_delay) * self._rng()
            remaining = delay - (self._clock() - self._last)
            if remaining > 0:
                self._sleep(remaining)
        self._last = self._clock()

    def mark(self) -> None:
        """Call after a page has loaded, so the next gap starts from the end of the load."""
        self._last = self._clock()


class BrowserSession:
    """Opens the product-scout Chrome profile in a visible window.

    The profile lives in browser-profile/, separate from the everyday Chrome profile,
    because Chrome locks a profile that is in use and refuses automation on the default one.
    """

    def __init__(self, profile_dir: Path, timer: PoliteTimer | None = None):
        self.profile_dir = profile_dir
        self.timer = timer or PoliteTimer()

    def __enter__(self) -> "BrowserSession":
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        try:
            self._ctx = self._pw.chromium.launch_persistent_context(
                str(self.profile_dir),
                channel="chrome",
                headless=False,
                locale="sv-SE",
                viewport={"width": 1366, "height": 900},
            )
        except Exception:
            # Often the profile is locked because another scout window still uses it.
            self._pw.stop()
            raise
        self._page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        return self

    def __exit__(self, *exc) -> None:
        try:
            self._ctx.close()
        finally:
            self._pw.stop()

    def fetch(self, url: str) -> RawPage:
        self.timer.wait()
        started = time.monotonic()
        try:
            response = self._page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            self._page.wait_for_timeout(1_500)
            status, html = (response.status if response else 0), self._page.content()
        except PlaywrightError:
            status, html = 0, ""
        finally:
            self.timer.mark()
        return RawPage(
            url=url,
            final_url=self._page.url,
            status=status,
            html=html,
            fetched_at=datetime.now(timezone.utc),
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    def screenshot(self, path: Path) -> Path:
        self._page.screenshot(path=str(path), full_page=False)
        return path

    def show(self, url: str) -> None:
        self._page.goto(url, wait_until="domcontentloaded")
        self._page.bring_to_front()
