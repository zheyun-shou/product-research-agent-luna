"""Test doubles shared by several test files."""
from datetime import datetime, timezone
from pathlib import Path

from scout.llm.client import CallRecord, LLMUnavailable
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
        self.calls = []

    def _reply(self, role, user, name):
        self.requests.append((role, user))
        reply = self.replies.pop(0)
        if isinstance(reply, LLMUnavailable):
            raise reply  # no reply came, so there is no call record
        self.calls.append(CallRecord(role, f"fake-{role}", 0, 0, 0.0, name, valid=not isinstance(reply, Exception)))
        if isinstance(reply, Exception):
            raise reply
        return reply

    def structured(self, role, system, user, schema):
        return self._reply(role, user, schema.__name__)

    def tools(self, role, system, user, tools, name="tools"):
        """The queued reply is the list of tool objects that the model calls."""
        return self._reply(role, user, name)

    def mark_checked(self, passed):
        if self.calls:
            self.calls[-1].checked = passed

    def model_for(self, role):
        return f"fake-{role}"


from contextlib import contextmanager


class FakeSpan:
    def __init__(self, events, kind):
        self.events, self.kind = events, kind

    def update(self, **fields):
        self.events.append(("update", self.kind, fields))


class FakeTracer:
    """Records turns, spans and generations as tuples in events."""

    enabled = True

    def __init__(self):
        self.events = []

    @contextmanager
    def _record(self, event):
        self.events.append(event)
        try:
            yield FakeSpan(self.events, event[0])
        except Exception:
            self.events.append(("error", event[0]))
            raise

    def turn(self, thread_id, text):
        return self._record(("turn", thread_id))

    def span(self, name, **fields):
        return self._record(("span", name))

    def generation(self, name, model, **fields):
        return self._record(("generation", name, model))

    def flush(self):
        self.events.append(("flush",))
