from scout.reddit import RedditReader, thread_id, thread_text
from scout.websearch import fetch_texts

URL = "https://www.reddit.com/r/headphones/comments/1AbC2d/sony_wh1000xm6_after_a_year/"
THREAD = [
    {"data": {"children": [{"kind": "t3", "data": {"title": "Sony WH-1000XM6 after a year", "subreddit": "headphones",
                                                    "score": 120, "selftext": "The hinge  cracked.",
                                                    "author": "someone"}}]}},
    {"data": {"children": [
        {"kind": "t1", "data": {"body": "Mine too, at the left hinge.", "score": 40, "author": "a",
                                "replies": {"data": {"children": [
                                    {"kind": "t1", "data": {"body": "Sony fixed it for free.", "score": 5,
                                                            "replies": ""}}]}}}},
        {"kind": "t1", "data": {"body": "[deleted]", "score": 1}},
        {"kind": "more", "data": {"children": ["x"]}}]}},
]


class Response:
    def __init__(self, status, data):
        self.status_code, self.data = status, data

    def json(self):
        return self.data

    def raise_for_status(self):
        if self.status_code != 200:
            raise RuntimeError(self.status_code)


def test_thread_id_only_for_a_thread():
    assert thread_id(URL) == "1abc2d"
    assert thread_id("https://old.reddit.com/r/x/comments/zz9/") == "zz9"
    assert thread_id("https://www.reddit.com/r/headphones/") is None
    assert thread_id("https://www.sweclockers.com/forum/trad/1") is None


def test_thread_text_has_the_post_and_the_comments_without_names():
    text = thread_text(THREAD)
    assert text == ("Sony WH-1000XM6 after a year\n[r/headphones, 120 points]\nThe hinge cracked.\n"
                    "[comment, 40 points] Mine too, at the left hinge.\n  [comment, 5 points] Sony fixed it for free.")
    assert "someone" not in text


def test_the_reader_gets_one_token_logs_each_request_and_keeps_the_gap():
    calls, log, slept = [], [], []
    now = [0.0]

    def post(url, data, auth, headers, timeout):
        calls.append(("post", url, auth))
        return Response(200, {"access_token": "T", "expires_in": 3600})

    def get(url, headers, timeout, params):
        calls.append(("get", url, headers["Authorization"]))
        return Response(200, THREAD)
    read = RedditReader("id", "secret", lambda *row: log.append(row[:4]), post, get, clock=lambda: now[0],
                        sleep=slept.append)
    assert read(URL).startswith("Sony WH-1000XM6 after a year")
    assert read(URL)
    assert read("https://example.com/a") is None
    assert [c[0] for c in calls] == ["post", "get", "get"] and calls[1][2] == "bearer T"
    assert calls[1][1] == "https://oauth.reddit.com/comments/1abc2d"
    assert [row[2:] for row in log] == [("ok", 200)] * 3
    assert slept  # the clock does not move, so each request after the first waits the gap


def test_a_failed_thread_gives_no_text_and_other_pages_still_load():
    def post(url, **kwargs):
        return Response(401, {})
    read = RedditReader("id", "bad", None, post, None, sleep=lambda s: None)
    assert read(URL) == ""

    class Page:
        html, final_url = "<html><body><p>A page about the hinge of the headphones.</p></body></html>", "https://a.se/x"

    class Got:
        page = Page()
    texts = fetch_texts(lambda u: Got(), [URL, "https://a.se/x"], read=read)
    assert URL not in texts and "hinge" in texts["https://a.se/x"]


def test_a_failed_log_does_not_lose_the_thread():
    def broken_log(*row):
        raise RuntimeError("database gone")

    def post(url, data, auth, headers, timeout):
        return Response(200, {"access_token": "T", "expires_in": 3600})

    def get(url, headers, timeout, params):
        return Response(200, THREAD)
    read = RedditReader("id", "secret", broken_log, post, get, sleep=lambda s: None)
    assert read(URL).startswith("Sony WH-1000XM6 after a year")
