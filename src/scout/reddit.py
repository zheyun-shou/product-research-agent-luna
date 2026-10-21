"""Reddit threads through the official Reddit API (plan 2026-10-20-review-sources.md). Reddit blocks the reading of
its pages, so without the API a web search gives only the short part of the search result.

The API needs an app of type "script" or "web app" at https://www.reddit.com/prefs/apps. I make it myself and put
REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET in .env. The program uses the app-only grant (no Reddit account, no
password) and reads only: the post and its comments. Each request goes to the fetch log."""
import contextlib
import os
import re
import threading
import time
from typing import Callable

import httpx

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
THREAD_URL = "https://oauth.reddit.com/comments/{id}"
USER_AGENT = "python:product-scout:0.1 (personal shopping research)"
TIMEOUT = 20.0
MAX_COMMENTS = 200
GAP = 1.0  # seconds between two requests; the free limit is 100 a minute
THREAD = re.compile(r"^https?://(?:[a-z]+\.)?reddit\.com/r/[^/]+/comments/([a-z0-9]+)", re.I)

Log = Callable[[str, str, str, int | None, int, str], None]  # source, url, status, http status, ms, note


def thread_id(url: str) -> str | None:
    found = THREAD.match(url)
    return found.group(1).lower() if found else None


def comments_of(listing: dict, depth: int = 0) -> list[tuple[int, int, str]]:
    """(depth, score, text) of each comment, in the order of the thread."""
    found = []
    for child in (listing or {}).get("data", {}).get("children", []):
        if child.get("kind") != "t1":
            continue
        data = child.get("data") or {}
        body = " ".join(str(data.get("body") or "").split())
        if body and body not in ("[deleted]", "[removed]"):
            found.append((depth, int(data.get("score") or 0), body))
        replies = data.get("replies")
        if isinstance(replies, dict):
            found += comments_of(replies, depth + 1)
    return found


def thread_text(data: list) -> str:
    """The text of a thread from the answer of /comments/{id}: the title, the post and the comments, without the
    names of the users."""
    post = ((data[0].get("data") or {}).get("children") or [{}])[0].get("data") or {}
    lines = [str(post.get("title") or "").strip(), f"[r/{post.get('subreddit', '')}, {post.get('score', 0)} points]"]
    body = " ".join(str(post.get("selftext") or "").split())
    if body:
        lines.append(body)
    for depth, score, text in comments_of(data[1] if len(data) > 1 else {}):
        lines.append(f"{'  ' * min(depth, 3)}[comment, {score} points] {text}")
    return "\n".join(line for line in lines if line)


class RedditReader:
    def __init__(self, client_id: str, secret: str, log: Log | None = None, post=httpx.post, get=httpx.get,
                 clock=time.monotonic, sleep=time.sleep):
        self.client_id, self.secret, self.log = client_id, secret, log or (lambda *a: None)
        self.post, self.get, self.clock, self.sleep = post, get, clock, sleep
        self._token, self._expires, self._last = "", 0.0, None
        self._lock = threading.Lock()

    def _wait(self) -> None:
        if self._last is not None and self.clock() - self._last < GAP:
            self.sleep(GAP - (self.clock() - self._last))
        self._last = self.clock()

    def _log(self, *row) -> None:
        with contextlib.suppress(Exception):  # the log must not lose a thread that was read
            self.log(*row)

    def _send(self, send, url: str, **kwargs):
        self._wait()
        start = self.clock()
        try:
            response = send(url, headers={"User-Agent": USER_AGENT, **kwargs.pop("headers", {})}, timeout=TIMEOUT,
                            **kwargs)
        except Exception as exc:
            self._log("reddit.com", url, "failed", None, round((self.clock() - start) * 1000),
                      f"{type(exc).__name__}: {exc}"[:300])
            raise
        self._log("reddit.com", url, "ok" if response.status_code == 200 else "failed", response.status_code,
                  round((self.clock() - start) * 1000), "")
        return response

    def token(self) -> str:
        if self._token and self.clock() < self._expires - 60:
            return self._token
        response = self._send(self.post, TOKEN_URL, data={"grant_type": "client_credentials"},
                              auth=(self.client_id, self.secret))
        response.raise_for_status()
        data = response.json()
        self._token, self._expires = data["access_token"], self.clock() + float(data.get("expires_in") or 3600)
        return self._token

    def __call__(self, url: str) -> str | None:
        """The text of a Reddit thread, "" when it could not be read, or None for an address that is not a thread."""
        found = thread_id(url)
        if found is None:
            return None
        with self._lock:  # one request at a time, with the gap
            try:
                response = self._send(self.get, THREAD_URL.format(id=found),
                                      headers={"Authorization": f"bearer {self.token()}"},
                                      params={"limit": MAX_COMMENTS, "sort": "top", "raw_json": 1})
                if response.status_code != 200:
                    return ""
                return thread_text(response.json())
            except Exception:  # the thread stays a short part of the search result
                return ""


def reddit_reader(log: Log | None = None) -> RedditReader | None:
    """A reader when REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET are set, else None."""
    client_id, secret = os.environ.get("REDDIT_CLIENT_ID", "").strip(), os.environ.get("REDDIT_CLIENT_SECRET", "")
    return RedditReader(client_id, secret.strip(), log) if client_id and secret.strip() else None
