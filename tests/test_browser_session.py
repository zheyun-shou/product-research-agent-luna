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


def test_cdp_endpoint_gives_chrome_an_ip_in_place_of_a_host_name(monkeypatch):
    import socket

    from scout.sources.browser import cdp_endpoint
    monkeypatch.setattr(socket, "gethostbyname", lambda host: {"host.docker.internal": "192.168.65.254"}[host])
    assert cdp_endpoint("http://host.docker.internal:9222") == "http://192.168.65.254:9222"
    assert cdp_endpoint("http://localhost:9222") == "http://localhost:9222"
    assert cdp_endpoint("http://127.0.0.1:9222/") == "http://127.0.0.1:9222/"
