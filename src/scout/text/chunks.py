"""Divide the text of a product page into chunks for retrieval (spec 4.4, text path)."""
import re
from dataclasses import dataclass, field
from typing import Callable

MAX_TOKENS = 500  # paragraphs are joined until the next one does not fit, so most long parts have 300 to 500
OVERLAP_TOKENS = 50

SENTENCE_END = re.compile(r"(?<=[.!?。！？])\s+")


def approx_tokens(text: str) -> int:
    """About four characters for each token. The live code counts with the bge-m3 tokenizer."""
    return max(1, len(text) // 4) if text else 0


@dataclass
class Chunk:
    kind: str  # specs, description, review, image_text
    ref: str  # where in the page, for example "review 3" or "description 2"
    text: str  # starts with the product title
    meta: dict = field(default_factory=dict)


def _pieces(text: str, count: Callable[[str], int], max_tokens: int) -> list[str]:
    """Paragraphs, and the sentences of a paragraph that is too long."""
    out = []
    for para in (p.strip() for p in re.split(r"\n\s*\n|\n", text)):
        if not para:
            continue
        if count(para) <= max_tokens:
            out.append(para)
            continue
        for sentence in SENTENCE_END.split(para):
            while count(sentence) > max_tokens:  # one very long sentence: cut it by characters
                cut = len(sentence) * max_tokens // count(sentence)
                out.append(sentence[:cut])
                sentence = sentence[cut:]
            if sentence.strip():
                out.append(sentence.strip())
    return out


def split_text(text: str, count: Callable[[str], int] = approx_tokens, max_tokens: int = MAX_TOKENS,
               overlap: int = OVERLAP_TOKENS) -> list[str]:
    """Join paragraphs into parts of at most max_tokens. Each new part starts with the last paragraph or
    sentence of the part before, if it is not longer than overlap."""
    parts, current = [], []
    for piece in _pieces(text, count, max_tokens):
        if current and count("\n".join(current + [piece])) > max_tokens:
            parts.append("\n".join(current))
            last = current[-1]
            current = [last] if count(last) <= overlap and count(last + "\n" + piece) <= max_tokens else []
        current.append(piece)
    if current:
        parts.append("\n".join(current))
    return parts


def review_text(review: dict) -> str:
    head = []
    if review.get("rating"):
        head.append(f"{review['rating']} of 5")
    if review.get("verified"):
        head.append("verified purchase")
    if review.get("date"):
        head.append(review["date"])
    if review.get("variant"):
        head.append(review["variant"])
    title = (review.get("title") or "").strip()
    body = (review.get("text") or "").strip()
    return f"Review ({', '.join(head)}): " + ". ".join(t for t in (title, body) if t)


def page_chunks(title: str, page: dict, count: Callable[[str], int] = approx_tokens,
                max_tokens: int = MAX_TOKENS) -> list[Chunk]:
    """The chunks of one stored page text (a row of page_texts). A review is one chunk unless it is very long.
    The author of a review is never kept."""
    prefix = f"Product: {title}\n"
    chunks = []
    lines = [f"{k}: {v}" for k, v in (page.get("specs") or {}).items()]
    lines += [f"- {b}" for b in page.get("bullets") or []]
    for i, part in enumerate(split_text("\n".join(lines), count, max_tokens), 1):
        chunks.append(Chunk("specs", f"specs {i}", prefix + part))
    for i, part in enumerate(split_text(page.get("description") or "", count, max_tokens), 1):
        chunks.append(Chunk("description", f"description {i}", prefix + part))
    refs = [f"review {n}" for n in range(1, len(page.get("reviews") or []) + 1)]
    return chunks + review_chunks(title, page.get("reviews") or [], refs, count, max_tokens)


def review_chunks(title: str, reviews: list[dict], refs: list[str], count: Callable[[str], int] = approx_tokens,
                  max_tokens: int = MAX_TOKENS) -> list[Chunk]:
    """One chunk for each review, unless it is very long. refs[i] names reviews[i]. No author is kept."""
    prefix = f"Product: {title}\n"
    chunks = []
    for ref, review in zip(refs, reviews):
        meta = {k: review[k] for k in ("rating", "date", "verified", "title", "filter", "variant")
                if review.get(k) not in (None, "")}
        parts = split_text(review_text(review), count, max_tokens)
        for i, part in enumerate(parts, 1):
            chunks.append(Chunk("review", ref if len(parts) == 1 else f"{ref} part {i}", prefix + part, meta))
    return chunks
