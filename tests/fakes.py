"""Test doubles shared by several test files."""
from datetime import datetime, timezone
from pathlib import Path

from scout.sources.base import RawPage

NOW = datetime(2026, 10, 3, 10, 15, tzinfo=timezone.utc)


class FakeBrowser:
    """Serves HTML by URL. A list gives one page per fetch; the last one repeats."""

    def __init__(self, pages: dict[str, list[str]]):
        self.pages = {url: list(htmls) for url, htmls in pages.items()}
        self.fetched: list[str] = []

    def fetch(self, url: str) -> RawPage:
        self.fetched.append(url)
        queue = self.pages[url]
        html = queue.pop(0) if len(queue) > 1 else queue[0]
        return RawPage(url=url, final_url=url, status=200, html=html, fetched_at=NOW, duration_ms=5)

    def screenshot(self, path: Path) -> Path:
        path.write_bytes(b"png")
        return path


class FakeLLM:
    """Returns queued replies (or raises queued exceptions) and records the prompts."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []

    def structured(self, role, system, user, schema):
        self.requests.append((role, user))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def model_for(self, role):
        return f"fake-{role}"
