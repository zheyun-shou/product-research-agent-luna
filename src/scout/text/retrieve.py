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


def _where(scope: Scope, table: str = "") -> tuple[str, list]:
    """The SQL condition of the scope on the chunks. `table` is the alias of the chunks with its dot, in a join."""
    parts, args = ["TRUE"], []
    if scope.listing_ids is not None:
        parts.append(f"{table}listing_id = ANY(%s)")
        args.append(scope.listing_ids)
    if scope.kinds is not None:
        parts.append(f"{table}kind = ANY(%s)")
        args.append(scope.kinds)
    if scope.category is not None:
        parts.append(f"{table}listing_id IN (SELECT l.id FROM listings l JOIN products p ON p.id = l.product_id"
                     " WHERE p.category = %s)")
        args.append(scope.category)
    if scope.since is not None:
        parts.append(f"{table}fetched_at >= %s")
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


class MultiQueryRetriever:
    """Several queries for one question, for example the same search words in Swedish, English and Chinese. Each
    query goes to each retriever, and RRF joins all lists. BM25 matches the words of the text in its own language;
    the vectors also match across languages."""

    def __init__(self, retrievers: list[Retriever], depth: int = 50):
        self.retrievers, self.depth = retrievers, depth
        self.name = "multi:" + "+".join(r.name for r in retrievers)

    def search_many(self, queries: list[str], scope: Scope, k: int = 10) -> list[Hit]:
        hits, scores = {}, Counter()
        for query in dict.fromkeys(q.strip() for q in queries if q.strip()):
            for retriever in self.retrievers:
                for rank, hit in enumerate(retriever.search(query, scope, self.depth), 1):
                    hits.setdefault(hit.chunk_id, hit)
                    scores[hit.chunk_id] += 1 / (RRF_K + rank)
        top = sorted(scores, key=lambda cid: -scores[cid])[:k]
        return [Hit(**{**hits[cid].__dict__, "score": scores[cid]}) for cid in top]

    def search(self, query: str, scope: Scope, k: int = 10) -> list[Hit]:
        return self.search_many([query], scope, k)


class LongContext:
    """The baseline: all chunks of the scope, in page order. k does not apply."""
    name = "long_context"

    def __init__(self, conn):
        self.conn = conn

    def search(self, query: str, scope: Scope, k: int = 10) -> list[Hit]:
        return [_hit(r) for r in _rows(self.conn, scope)]


@dataclass
class Similar:
    listing_id: int  # the first listing of the product
    score: float  # the sum of the cosine similarities to the product and to the wish
    reason: Hit  # the chunk of the product that is nearest to the wish, or to the product without a wish


class NoText(Exception):
    """The product to compare with has no indexed text."""


def _mean(vectors: list[list[float]]) -> list[float]:
    return [sum(xs) / len(vectors) for xs in zip(*vectors)]


def similar_products(conn, embedder: Embedder, like: list[int], wishes: list[str], scope: Scope, k: int = 10,
                     exclude: set[int] | frozenset = frozenset()) -> list[Similar]:
    """The products that are near the products of the listings `like` and near the soft wish (the same wish in more
    languages). Each product has one vector, the mean of the vectors of the chunks of all its listings. The products
    of `like` and the product ids in `exclude` are left out. The reason of each product is its chunk that is nearest
    to the wish. Raises NoText when `like` has no indexed chunks."""
    targets = []
    if like:
        row = conn.execute(
            """SELECT avg(c.embedding)::text AS v FROM chunks c JOIN listings l ON l.id = c.listing_id
               WHERE c.embedding IS NOT NULL
                 AND l.product_id IN (SELECT product_id FROM listings WHERE id = ANY(%s))""", [like]).fetchone()
        if row["v"] is None:
            raise NoText(f"no indexed text for id {', '.join(map(str, like))}")
        targets.append(row["v"])
    wishes = list(dict.fromkeys(w.strip() for w in wishes if w.strip()))
    if wishes:
        targets.append(str(_mean([e.dense for e in embedder.embed(wishes)])))
    if not targets:
        return []
    where, args = _where(scope, "c.")
    near = " + ".join("(1 - (v <=> %s::vector))" for _ in targets)
    rows = conn.execute(
        f"""WITH m AS (SELECT l.product_id, min(c.listing_id) AS listing_id, avg(c.embedding) AS v
                       FROM chunks c JOIN listings l ON l.id = c.listing_id
                       WHERE c.embedding IS NOT NULL AND {where}
                         AND l.product_id <> ALL(%s)
                         AND l.product_id NOT IN (SELECT product_id FROM listings WHERE id = ANY(%s))
                       GROUP BY l.product_id)
            SELECT product_id, listing_id, {near} AS score FROM m ORDER BY score DESC, product_id LIMIT %s""",
        [*args, sorted(exclude), like, *targets, k]).fetchall()
    if not rows:
        return []
    where, args = _where(Scope(kinds=scope.kinds, since=scope.since), "c.")
    best = conn.execute(
        f"""SELECT DISTINCT ON (l.product_id) c.id, c.listing_id, c.kind, c.ref, c.text, c.meta, l.product_id,
                   c.embedding <=> %s::vector AS distance
            FROM chunks c JOIN listings l ON l.id = c.listing_id
            WHERE c.embedding IS NOT NULL AND l.product_id = ANY(%s) AND {where}
            ORDER BY l.product_id, distance, c.id""",
        [targets[-1], [r["product_id"] for r in rows], *args]).fetchall()
    reasons = {r["product_id"]: _hit(r, 1 - r["distance"]) for r in best}
    return [Similar(r["listing_id"], r["score"], reasons[r["product_id"]]) for r in rows]


def wish_matches(conn, embedder: Embedder, listing_ids: list[int], wishes: list[str],
                 n: int = 3) -> dict[int, list[list[Hit]]]:
    """For each listing and each wish, the n chunks of its product (all its listings) that are nearest to the wish,
    nearest first, with the cosine similarity as the score. The outer list of a listing has the order of the wishes.
    A listing without indexed chunks is not in the result. Near means the same topic: a review that says "very
    loud" is near the wish "quiet", so a model must judge the chunks."""
    wishes = [w for w in wishes if w.strip()]
    if not listing_ids or not wishes:
        return {}
    out: dict[int, list[list[Hit]]] = {}
    for i, vector in enumerate(str(e.dense) for e in embedder.embed(wishes)):
        rows = conn.execute(
            """WITH asked AS (SELECT id AS listing_id, product_id FROM listings WHERE id = ANY(%s)),
                    near AS (SELECT a.listing_id AS asked, c.id, c.listing_id, c.kind, c.ref, c.text, c.meta,
                                    c.embedding <=> %s::vector AS distance,
                                    row_number() OVER (PARTITION BY a.listing_id
                                                       ORDER BY c.embedding <=> %s::vector, c.id) AS place
                             FROM asked a JOIN listings l ON l.product_id = a.product_id
                             JOIN chunks c ON c.listing_id = l.id WHERE c.embedding IS NOT NULL)
               SELECT * FROM near WHERE place <= %s ORDER BY asked, place""",
            [listing_ids, vector, vector, n]).fetchall()
        for r in rows:
            lists = out.setdefault(r["asked"], [[] for _ in wishes])
            lists[i].append(_hit(r, 1 - r["distance"]))
    return out


def hybrid_search(conn, make_embedder) -> Callable[[str, Scope, int], list[Hit]]:
    """The search of the search_text tool: vectors and BM25 with RRF, for each query (since 2026-10-13: the
    multi-query method was best in eval-retrieval). The embedder loads at the first search.
    Chunks of newly stored pages get their vectors before each search. `search.similar` and `search.wishes` are
    similar_products and wish_matches with the same embedder."""
    from scout.text.index import index_chunks
    loaded: list = []

    def search(query: str | list[str], scope: Scope, k: int = 10, index: bool = True) -> list[Hit]:
        """A list gives the same search words in more languages (MultiQueryRetriever). index=False skips the
        indexing, for the second and later searches of one step."""
        if not loaded:
            loaded.append(make_embedder())
        if index:
            index_chunks(conn, loaded[0])
        queries = [query] if isinstance(query, str) else query
        return MultiQueryRetriever([VectorRetriever(conn, loaded[0]), Bm25Retriever(conn, loaded[0])]).search_many(
            queries, scope, k)

    def similar(like: list[int], wishes: list[str], scope: Scope, k: int = 10,
                exclude: set[int] | frozenset = frozenset()) -> list[Similar]:
        """similar_products with the same embedder and the same indexing (the similar_products tool)."""
        if not loaded:
            loaded.append(make_embedder())
        index_chunks(conn, loaded[0])
        return similar_products(conn, loaded[0], like, wishes, scope, k, exclude)
    def wishes(listing_ids: list[int], wishes: list[str]) -> dict[int, list[list[Hit]]]:
        """wish_matches with the same embedder and the same indexing (find_products with preferences)."""
        if not loaded:
            loaded.append(make_embedder())
        index_chunks(conn, loaded[0])
        return wish_matches(conn, loaded[0], listing_ids, wishes)
    search.similar, search.wishes = similar, wishes
    return search
