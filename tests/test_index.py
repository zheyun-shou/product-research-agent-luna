import pytest

from scout.db.store import save_record
from scout.text.embed import bm25_tokens, model_numbers
from scout.text.index import index_chunks
from tests.fakes import FakeEmbedder
from tests.test_store import record


def test_model_numbers_are_whole_tokens():
    assert model_numbers("Samsung RB38C7B5D22/EF kyl, 186 cm, A++") == ["RB38C7B5D22/EF", "RB38C7B5D22"]
    assert model_numbers("Höjd 186 cm, 2 dörrar") == []
    assert model_numbers("modell KG39NXIBF") == ["KG39NXIBF"]


def test_bm25_tokens_are_lowercase_words_and_model_numbers():
    assert bm25_tokens(FakeEmbedder(), "Tyst KG39NXIBF") == ["tyst", "kg39nxibf", "KG39NXIBF"]


@pytest.mark.db
def test_index_gives_each_chunk_a_vector_once(db):
    save_record(db, record(), "fridge")
    embedder = FakeEmbedder()
    report = index_chunks(db, embedder, batch=2)
    n = db.execute("SELECT count(*) AS n FROM chunks").fetchone()["n"]
    assert report.chunks == n and len(embedder.embedded) == n
    row = db.execute("SELECT embedding, sparse, tokens, embed_model FROM chunks LIMIT 1").fetchone()
    assert row["embed_model"] == "fake-embedder" and row["tokens"] and row["sparse"]
    assert index_chunks(db, embedder).chunks == 0
