"""The sites of the review searches (config/review_sources.yaml): Swedish shops for buyer reviews, test sites and
forums. search_web with a kind of source searches only on its sites, with OR in one search."""
import re
from functools import cache
from pathlib import Path

import yaml

PATH = Path(__file__).resolve().parents[2] / "config" / "review_sources.yaml"
KINDS = ("shops", "tests", "forums")


@cache
def load(path: Path = PATH) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}


def sites(kind: str, category: str | None, config: dict | None = None) -> list[str]:
    """The sites of a kind; the test sites are those of all categories and of the category."""
    config = load() if config is None else config
    value = config.get(kind) or []
    if isinstance(value, dict):
        found = [*(value.get("all") or []), *((value.get(category) or []) if category else [])]
    else:
        found = list(value)
    return list(dict.fromkeys(found))


def site_query(question: str, kind: str, category: str | None, config: dict | None = None) -> str:
    """The search words with the sites of the kind: "words (site:a OR site:b)". "any" or no sites: the words."""
    if kind not in KINDS:
        return question
    chosen = sites(kind, category, config)
    if not chosen:
        return question
    return f"{question} (" + " OR ".join(f"site:{s}" for s in chosen) + ")"


def is_forum(url: str, config: dict | None = None) -> bool:
    """The address is on a forum of the list (a site, or a site and the start of a path)."""
    from urllib.parse import urlsplit
    parts = urlsplit(url)
    where = parts.netloc.lower().removeprefix("www.") + parts.path
    return any(where == f or where.startswith(f.rstrip("/") + "/") for f in sites("forums", None, config))


def not_for_tests(config: dict | None = None) -> str | None:
    """A regular expression for the search words of a search of shops or forums only: such a search does not
    count as a web search for tests (the web floor)."""
    chosen = [*sites("shops", None, config), *sites("forums", None, config)]
    return r"\(site:(?:" + "|".join(re.escape(s) for s in chosen) + ")" if chosen else None
