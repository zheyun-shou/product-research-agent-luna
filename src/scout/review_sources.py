"""The sites of the review searches (config/review_sources.yaml): Swedish shops for buyer reviews, test sites and
forums. search_web with a kind of source searches only on its sites, with OR in one search."""
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
