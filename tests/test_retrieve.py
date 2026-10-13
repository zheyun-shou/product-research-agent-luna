import pytest

from scout.db.store import save_chunks, save_record
from scout.text.chunks import Chunk
from scout.text.index import index_chunks
from scout.text.retrieve import (Bm25Retriever, HybridRetriever, LongContext, Scope, SparseRetriever,
                                 VectorRetriever, bm25_scores)
from tests.fakes import FakeEmbedder
from tests.test_store import NOW, record


def test_bm25_prefers_rare_words_and_short_documents():
    docs = [["tyst", "kyl"], ["kyl", "kyl", "frys"], ["tyst", "kyl", "a", "b", "c", "d"]]
    scores = bm25_scores(["tyst"], docs)
    assert scores[1] == 0 and scores[0] > scores[2] > 0
    assert bm25_scores(["okänd"], docs) == [0, 0, 0] and bm25_scores(["x"], []) == []


@pytest.fixture
def corpus(db):
    a = save_record(db, record(asin="B0AAAAAAA1"), "fridge").listing_id
    b = save_record(db, record(asin="B0AAAAAAA2").model_copy(update={"model": "KG39NXIBF"}), "fridge").listing_id
    save_chunks(db, a, [Chunk("review", "review 1", "Product: A\nThe compressor is loud at night"),
                        Chunk("review", "review 2", "Product: A\nNice white door"),
                        Chunk("specs", "specs 1", "Product: A\nModel RB38C7B5D22 height 186 cm")], NOW)
    save_chunks(db, b, [Chunk("review", "review 1", "Product: B\nVery quiet, I hear nothing at night"),
                        Chunk("specs", "specs 1", "Product: B\nModel KG39NXIBF height 203 cm")], NOW)
    embedder = FakeEmbedder()
    index_chunks(db, embedder)
    return db, embedder, a, b


@pytest.mark.db
def test_each_method_finds_the_chunk_with_the_words(corpus):
    db, embedder, a, b = corpus
    for retriever in (VectorRetriever(db, embedder), Bm25Retriever(db, embedder), SparseRetriever(db, embedder)):
        top = retriever.search("loud compressor", Scope(), k=3)
        assert top[0].text.endswith("The compressor is loud at night"), retriever.name


@pytest.mark.db
def test_a_model_number_finds_its_specs(corpus):
    db, embedder, a, b = corpus
    top = Bm25Retriever(db, embedder).search("kg39nxibf", Scope(), k=1)
    assert top[0].listing_id == b and top[0].kind == "specs"


@pytest.mark.db
def test_the_scope_limits_listings_and_kinds(corpus):
    db, embedder, a, b = corpus
    for retriever in (VectorRetriever(db, embedder), Bm25Retriever(db, embedder)):
        hits = retriever.search("night", Scope(listing_ids=[b], kinds=["review"]), k=5)
        assert hits and {h.listing_id for h in hits} == {b} and {h.kind for h in hits} == {"review"}


@pytest.mark.db
def test_rrf_adds_the_ranks_of_both_lists(corpus):
    db, embedder, a, b = corpus
    hybrid = HybridRetriever([VectorRetriever(db, embedder), Bm25Retriever(db, embedder)])
    top = hybrid.search("loud compressor", Scope(), k=2)
    assert hybrid.name == "vector+bm25" and top[0].text.endswith("loud at night")
    assert top[0].score == pytest.approx(2 / 61)


@pytest.mark.db
def test_long_context_gives_all_chunks_of_the_scope(corpus):
    db, embedder, a, b = corpus
    assert [h.ref for h in LongContext(db).search("anything", Scope(listing_ids=[a]))] == [
        "review 1", "review 2", "specs 1"]


@pytest.mark.db
def test_more_queries_join_with_rrf(corpus):
    from scout.text.retrieve import MultiQueryRetriever
    db, embedder, a, b = corpus
    multi = MultiQueryRetriever([Bm25Retriever(db, embedder)])
    top = multi.search_many(["kg39nxibf", "loud compressor", "  ", "kg39nxibf"], Scope(), k=2)
    assert {h.text.split("\n")[1] for h in top} == {"Model KG39NXIBF height 203 cm", "The compressor is loud at night"}
    assert multi.name == "multi:bm25" and multi.search("kg39nxibf", Scope(), k=1)[0].listing_id == b
