"""Save fetched pages locally so parsing and evaluation can be rerun. Snapshots stay out of Git."""
import re
from pathlib import Path

from scout.sources.base import RawPage


def save_html(page: RawPage, base: Path, source: str, kind: str, key: str) -> Path:
    safe_key = re.sub(r"[^\w.-]+", "_", key).strip("_")[:80] or "page"
    path = base / source / kind / safe_key / f"{page.fetched_at.strftime('%Y%m%dT%H%M%S')}.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page.html, encoding="utf-8")
    return path
