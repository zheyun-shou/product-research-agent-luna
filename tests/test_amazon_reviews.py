from datetime import datetime, timezone
from pathlib import Path

from scout.sources.amazon_se.check import check_page
from scout.sources.amazon_se.reviews import parse_review_page
from scout.sources.amazon_se.urls import reviews_url
from scout.sources.base import RawPage

FIXTURES = Path(__file__).parent / "fixtures" / "amazon_se"
HTML = (FIXTURES / "reviews_critical.html").read_text(encoding="utf-8")
URL = reviews_url("B0AAAAAAA1", "critical")
NOW = datetime(2026, 10, 6, 10, 0, tzinfo=timezone.utc)


def test_reviews_and_matching_count():
    reviews, matching = parse_review_page(HTML)
    assert matching == 113
    assert [r["review_id"] for r in reviews] == ["R1AAAAAAAAAAAA", "R2BBBBBBBBBBBB"]  # the empty review is left out
    first, second = reviews
    assert first == {
        "review_id": "R1AAAAAAAAAAAA", "title": "Zu laut und zu leicht", "rating": "3.0",
        "date": "Reviewed in Germany on 24 August 2026", "verified": True, "variant": "Colour Name: Colour: Red",
        "text": "Ist für seine angeblichen 39 Dezibel zu laut.",
    }
    assert (second["title"], second["rating"], second["verified"]) == ("Kom trasig", "1.0", False)


def test_no_reviewer_name_and_no_translation():
    reviews, _ = parse_review_page(HTML)
    stored = str(reviews)
    assert "Person" not in stored
    assert "Too loud" not in stored


def test_check_review_page():
    ok = RawPage(url=URL, final_url=URL, status=200, html=HTML, fetched_at=NOW)
    assert check_page(ok).ok
    empty = ok.model_copy(update={"html": "<html><body>nav only</body></html>"})
    assert check_page(empty).problem == "incomplete"
    signin = ok.model_copy(update={"final_url": "https://www.amazon.se/ap/signin?openid=x"})
    assert check_page(signin).problem == "signin"
