"""Compare the retrieval methods on a fixed question set (spec section 7, retrieval quality).

Each question names its gold chunks by ASIN and ref, with the exact words that hold the answer. Python checks that
the words are in the chunk, so a rebuilt chunk table cannot make a gold label wrong without notice."""
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from scout.text.retrieve import Retriever, Scope

QUESTIONS = Path(__file__).resolve().parents[3] / "eval" / "retrieval" / "questions.yaml"


@dataclass
class Gold:
    asin: str
    ref: str
    quote: str


@dataclass
class Question:
    id: str
    question: str
    language: str
    gold: list[Gold]
    asins: list[str] = field(default_factory=list)  # empty: all stored products of the category
    kinds: list[str] = field(default_factory=list)
    category: str = "fridge"


def load_questions(path: Path = QUESTIONS) -> list[Question]:
    rows = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [Question(**{**r, "gold": [Gold(**g) for g in r["gold"]]}) for r in rows]


def gold_ids(conn, q: Question) -> tuple[set[int], list[str]]:
    """The chunk ids of the gold labels, and the problems: a missing chunk or a quote that is not in it."""
    ids, problems = set(), []
    for g in q.gold:
        row = conn.execute(
            """SELECT c.id, c.text FROM chunks c JOIN listings l ON l.id = c.listing_id
               WHERE l.source_id = %s AND c.ref = %s""", (g.asin, g.ref)).fetchone()
        if row is None:
            problems.append(f"{q.id}: no chunk {g.asin} {g.ref}")
        elif g.quote not in row["text"]:
            problems.append(f"{q.id}: the words '{g.quote}' are not in {g.asin} {g.ref}")
        else:
            ids.add(row["id"])
    return ids, problems


def scope_of(conn, q: Question) -> Scope:
    listing_ids = None
    if q.asins:
        rows = conn.execute("SELECT id FROM listings WHERE source_id = ANY(%s)", (q.asins,)).fetchall()
        listing_ids = [r["id"] for r in rows]
    return Scope(listing_ids=listing_ids, kinds=q.kinds or None, category=q.category)


@dataclass
class MethodScore:
    method: str
    questions: int = 0
    recall: float = 0.0  # mean share of the gold chunks in the results
    hit: float = 0.0  # share of questions with at least one gold chunk in the results
    mrr: float = 0.0  # mean of 1 / rank of the first gold chunk
    tokens: float = 0.0  # mean tokens of the result text, the size it adds to a prompt
    seconds: float = 0.0  # mean time of one search
    misses: list[str] = field(default_factory=list)


def score_method(conn, retriever: Retriever, questions: list[Question], count, k: int = 10) -> MethodScore:
    s = MethodScore(retriever.name)
    for q in questions:
        gold, problems = gold_ids(conn, q)
        if problems or not gold:
            continue
        start = time.perf_counter()
        hits = retriever.search(q.question, scope_of(conn, q), k)
        s.seconds += time.perf_counter() - start
        ids = [h.chunk_id for h in hits]
        found = gold & set(ids)
        s.questions += 1
        s.recall += len(found) / len(gold)
        s.hit += bool(found)
        s.mrr += next((1 / (i + 1) for i, cid in enumerate(ids) if cid in gold), 0.0)
        s.tokens += sum(count(h.text) for h in hits)
        if not found:
            s.misses.append(q.id)
    if s.questions:
        for name in ("recall", "hit", "mrr", "tokens", "seconds"):
            setattr(s, name, getattr(s, name) / s.questions)
    return s


def scores_table(scores: list[MethodScore], k: int) -> str:
    lines = [f"| Method | Questions | Recall@{k} | Hit@{k} | MRR | Tokens | Seconds |", "|---|---|---|---|---|---|---|"]
    lines += [f"| {s.method} | {s.questions} | {s.recall:.2f} | {s.hit:.2f} | {s.mrr:.2f} | {s.tokens:.0f} | "
              f"{s.seconds:.3f} |" for s in scores]
    return "\n".join(lines)
