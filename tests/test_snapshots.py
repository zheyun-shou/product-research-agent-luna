from datetime import datetime, timezone

from scout.sources.base import RawPage
from scout.sources.snapshots import save_html

NOW = datetime(2026, 10, 3, 10, 15, 0, tzinfo=timezone.utc)


def test_save_html_writes_under_source_kind_and_key(tmp_path):
    page = RawPage(url="u", final_url="u", status=200, html="<p>kylskåp</p>", fetched_at=NOW)
    path = save_html(page, tmp_path, "amazon_se", "search", "kylskåp-p1")
    assert path == tmp_path / "amazon_se" / "search" / "kylskåp-p1" / "20261003T101500.html"
    assert path.read_text(encoding="utf-8") == "<p>kylskåp</p>"


def test_save_html_makes_unsafe_keys_safe(tmp_path):
    page = RawPage(url="u", final_url="u", status=200, html="x", fetched_at=NOW)
    path = save_html(page, tmp_path, "amazon_se", "search", 'a/b:c?"d')
    assert path.parent.name == "a_b_c_d"
