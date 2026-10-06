"""Test doubles shared by several test files."""
import json
from datetime import datetime, timezone
from pathlib import Path

from scout.llm.client import CallRecord, LLMUnavailable
from scout.sources.base import RawPage

NOW = datetime(2026, 10, 3, 10, 15, tzinfo=timezone.utc)


SERVER_ERROR = "<503>"  # a page in FakeBrowser that comes with the status 503


class FakeBrowser:
    """Serves HTML by URL. A list gives one page per fetch; the last one repeats."""

    def __init__(self, pages: dict[str, list[str]]):
        self.pages = {url: list(htmls) for url, htmls in pages.items()}
        self.fetched: list[str] = []

    def fetch(self, url: str) -> RawPage:
        self.fetched.append(url)
        queue = self.pages[url]
        html = queue.pop(0) if len(queue) > 1 else queue[0]
        status = 503 if html == SERVER_ERROR else 200
        return RawPage(url=url, final_url=url, status=status, html=html, fetched_at=NOW, duration_ms=5)

    def screenshot(self, path: Path) -> Path:
        path.write_bytes(b"png")
        return path


def output_text(reply) -> str:
    if isinstance(reply, list):
        return json.dumps([[type(c).__name__, c.model_dump(mode="json")] for c in reply])
    if hasattr(reply, "model_dump_json"):
        return reply.model_dump_json()
    return str(reply)


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
        self.calls.append(CallRecord(role, f"fake-{role}", 0, 0, 0.0, name, valid=not isinstance(reply, Exception),
                                     output=output_text(reply)))
        if isinstance(reply, Exception):
            raise reply
        return reply

    def structured(self, role, system, user, schema, images=()):
        self.images = list(images)
        return self._reply(role, user, schema.__name__)

    def tools(self, role, system, user, tools, name="tools", text_reply=None, history=()):
        """The queued reply is the list of tool objects that the model calls, or a str for a text answer.
        The request text has the user message and then the history, so a test can look for a tool result in it."""
        self.histories = getattr(self, "histories", []) + [list(history)]
        seen = [m.get("content") or json.dumps(m.get("tool_calls"), ensure_ascii=False) for m in history]
        reply = self._reply(role, "\n\n".join([user, *seen]), name)
        return [text_reply(text=reply)] if isinstance(reply, str) and text_reply else reply

    def mark_checked(self, passed, problems=()):
        if self.calls:
            self.calls[-1].checked = passed
            self.calls[-1].problems = list(problems)

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


class FakeEmbedder:
    """Words in place of subwords, and a hashed bag of words in place of the bge-m3 vector, so that texts with
    the same words are near each other."""

    name = "fake-embedder"

    def __init__(self):
        self.embedded: list[str] = []

    def subwords(self, text: str) -> list[str]:
        import re
        return re.findall(r"\w+", text.lower())

    def count(self, text: str) -> int:
        return len(text.split())

    def embed(self, texts):
        import hashlib
        import math

        from scout.text.embed import Embedded
        out = []
        for text in texts:
            self.embedded.append(text)
            words = self.subwords(text)
            dense = [0.0] * 1024
            for w in words:
                dense[int(hashlib.md5(w.encode()).hexdigest(), 16) % 1024] += 1.0
            norm = math.sqrt(sum(x * x for x in dense)) or 1.0
            out.append(Embedded([x / norm for x in dense], {w: 1.0 for w in words}))
        return out
