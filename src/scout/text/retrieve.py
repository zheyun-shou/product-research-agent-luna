"""Text retrieval over the chunks (spec 4.6): vectors, BM25, the sparse weights of bge-m3, RRF, and the long
context baseline. All methods have one interface, so the evaluation can compare them."""
import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Protocol

from scout.text.embed import Embedder, bm25_tokens

RRF_K = 60


@dataclass
class Hit:
    chunk_id: int
    listing_id: int
    kind: str
    ref: str
    text: str
    meta: dict = field(default_factory=dict)
    score: float = 0.0


@dataclass
class Scope:
    listing_ids: list[int] | None = None  # None: all listings
    kinds: list[str] | None = None  # None: all kinds
    since: datetime | None = None
    category: str | None = None  # only the listings of products of this category


class Retriever(Protocol):
    name: str

    def search(self, query: str, scope: Scope, k: int = 10) -> list[Hit]: ...


def _where(scope: Scope) -> tuple[str, list]:
    parts, args = ["TRUE"], []
    if scope.listing_ids is not None:
        parts.append("listing_id = ANY(%s)")
        args.append(scope.listing_ids)
    if scope.kinds is not None:
        parts.append("kind = ANY(%s)")
        args.append(scope.kinds)
    if scope.category is not None:
        parts.append("listing_id IN (SELECT l.id FROM listings l JOIN products p ON p.id = l.product_id"
                     " WHERE p.category = %s)")
        args.append(scope.category)
    if scope.since is not None:
        parts.append("fetched_at >= %s")
        args.append(scope.since)
    return " AND ".join(parts), args


def _hit(row: dict, score: float = 0.0) -> Hit:
    return Hit(row["id"], row["listing_id"], row["kind"], row["ref"], row["text"], row["meta"] or {}, score)


def _rows(conn, scope: Scope, extra: str = "") -> list[dict]:
    where, args = _where(scope)
    return conn.execute(f"SELECT id, listing_id, kind, ref, text, meta{extra} FROM chunks WHERE {where} ORDER BY id",
                        args).fetchall()


def bm25_scores(query: list[str], docs: list[list[str]], k1: float = 1.2, b: float = 0.75) -> list[float]:
    """Okapi BM25 of each document for the query. The statistics come from the documents given."""
    if not docs:
        return []
    n = len(docs)
    avg = sum(len(d) for d in docs) / n or 1.0
    df = Counter(t for d in docs for t in set(d))
    idf = {t: math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5)) for t in set(query) if df[t]}
    scores = []
    for d in docs:
        tf = Counter(d)
        scores.append(sum(idf[t] * tf[t] * (k1 + 1) / (tf[t] + k1 * (1 - b + b * len(d) / avg))
                          for t in query if t in idf and tf[t]))
    return scores


def _top(rows: list[dict], scores: list[float], k: int) -> list[Hit]:
    ranked = sorted(zip(rows, scores), key=lambda rs: -rs[1])
    return [_hit(r, s) for r, s in ranked[:k] if s > 0]


class VectorRetriever:
    name = "vector"

    def __init__(self, conn, embedder: Embedder):
        self.conn, self.embedder = conn, embedder

    def search(self, query: str, scope: Scope, k: int = 10) -> list[Hit]:
        vector = str(self.embedder.embed([query])[0].dense)
        where, args = _where(scope)
        # With strict filters the HNSW index can give too few rows; the iterative scan of pgvector 0.8 reads on.
        self.conn.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
        rows = self.conn.execute(
            f"""WITH near AS MATERIALIZED (
                    SELECT id, listing_id, kind, ref, text, meta, embedding <=> %s::vector AS distance FROM chunks
                    WHERE embedding IS NOT NULL AND {where} ORDER BY distance LIMIT %s)
                SELECT * FROM near ORDER BY distance""",
            [vector, *args, k]).fetchall()
        return [_hit(r, 1 - r["distance"]) for r in rows]


class Bm25Retriever:
    name = "bm25"

    def __init__(self, conn, embedder: Embedder):
        self.conn, self.embedder = conn, embedder

    def search(self, query: str, scope: Scope, k: int = 10) -> list[Hit]:
        rows = [r for r in _rows(self.conn, scope, ", tokens") if r["tokens"]]
        return _top(rows, bm25_scores(bm25_tokens(self.embedder, query), [r["tokens"] for r in rows]), k)


class SparseRetriever:
    """The sparse output of bge-m3: the sum of query weight times chunk weight over the shared tokens."""
    name = "sparse"

    def __init__(self, conn, embedder: Embedder):
        self.conn, self.embedder = conn, embedder

    def search(self, query: str, scope: Scope, k: int = 10) -> list[Hit]:
        weights = self.embedder.embed([query])[0].sparse
        rows = [r for r in _rows(self.conn, scope, ", sparse") if r["sparse"]]
        scores = [sum(w * r["sparse"].get(t, 0.0) for t, w in weights.items()) for r in rows]
        return _top(rows, scores, k)


class HybridRetriever:
    """Reciprocal Rank Fusion: each result gets the sum of 1/(60 + rank) over the lists."""

    def __init__(self, retrievers: list[Retriever], depth: int = 50):
        self.retrievers, self.depth = retrievers, depth
        self.name = "+".join(r.name for r in retrievers)

    def search(self, query: str, scope: Scope, k: int = 10) -> list[Hit]:
        hits, scores = {}, Counter()
        for retriever in self.retrievers:
            for rank, hit in enumerate(retriever.search(query, scope, self.depth), 1):
                hits.setdefault(hit.chunk_id, hit)
                scores[hit.chunk_id] += 1 / (RRF_K + rank)
        top = sorted(scores, key=lambda cid: -scores[cid])[:k]
        return [Hit(**{**hits[cid].__dict__, "score": scores[cid]}) for cid in top]


class LongContext:
    """The baseline: all chunks of the scope, in page order. k does not apply."""
    name = "long_context"

    def __init__(self, conn):
        self.conn = conn

    def search(self, query: str, scope: Scope, k: int = 10) -> list[Hit]:
        return [_hit(r) for r in _rows(self.conn, scope)]


def hybrid_search(conn, make_embedder) -> Callable[[str, Scope, int], list[Hit]]:
    """The search of the search_text tool: vectors and BM25 with RRF. The embedder loads at the first search.
    Chunks of newly stored pages get their vectors before each search."""
    from scout.text.index import index_chunks
    loaded: list = []

    def search(query: str, scope: Scope, k: int = 10) -> list[Hit]:
        if not loaded:
            loaded.append(make_embedder())
        index_chunks(conn, loaded[0])
        return HybridRetriever([VectorRetriever(conn, loaded[0]), Bm25Retriever(conn, loaded[0])]).search(
            query, scope, k)
    return search
