import pytest

from scout.sources import browser
from scout.sources.browser import BrowserSession


class FakePlaywright:
    def __init__(self):
        self.stopped = False
        self.chromium = self

    def start(self):
        return self

    def launch_persistent_context(self, *args, **kwargs):
        raise RuntimeError("profile is locked")

    def stop(self):
        self.stopped = True


def test_playwright_stops_when_chrome_does_not_start(monkeypatch, tmp_path):
    fake = FakePlaywright()
    monkeypatch.setattr(browser, "sync_playwright", lambda: fake)
    with pytest.raises(RuntimeError):
        with BrowserSession(tmp_path / "profile"):
            pass
    assert fake.stopped
