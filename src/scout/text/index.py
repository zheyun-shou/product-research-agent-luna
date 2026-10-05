"""Give the stored chunks their vectors and BM25 tokens."""
import time
from dataclasses import dataclass

from scout.db.store import chunks_to_embed, save_vectors
from scout.text.embed import Embedder, bm25_tokens


@dataclass
class IndexReport:
    chunks: int
    seconds: float


def index_chunks(conn, embedder: Embedder, batch: int = 64) -> IndexReport:
    """Embed each chunk that has no vector of this model, in batches, and commit after each batch."""
    start, done = time.perf_counter(), 0
    while rows := chunks_to_embed(conn, embedder.name, batch):
        vectors = embedder.embed([r["text"] for r in rows])
        save_vectors(conn, [(r["id"], v.dense, v.sparse, bm25_tokens(embedder, r["text"]))
                            for r, v in zip(rows, vectors)], embedder.name)
        conn.commit()
        done += len(rows)
    return IndexReport(done, time.perf_counter() - start)
