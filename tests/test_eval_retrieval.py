import pytest

from scout.eval.retrieval import Gold, Question, gold_ids, load_questions, score_method, scores_table
from scout.text.retrieve import Bm25Retriever, LongContext
from tests.test_retrieve import corpus  # noqa: F401  (fixture)

pytestmark = pytest.mark.db


def q(id, question, ref="review 1", quote="loud", asins=()):
    return Question(id, question, "en", [Gold("B0AAAAAAA1", ref, quote)], list(asins))


def test_a_quote_that_is_not_in_the_chunk_is_a_problem(corpus):
    db, *_ = corpus
    ids, problems = gold_ids(db, q("q1", "x", quote="quiet"))
    assert not ids and problems == ["q1: the words 'quiet' are not in B0AAAAAAA1 review 1"]
    assert gold_ids(db, q("q2", "x", ref="review 9"))[1] == ["q2: no chunk B0AAAAAAA1 review 9"]


def test_scores_of_bm25_and_long_context(corpus):
    db, embedder, a, b = corpus
    questions = [q("q1", "loud compressor"), q("q2", "white door", ref="review 2", quote="white door"),
                 q("q3", "zzz unknown words")]
    bm25 = score_method(db, Bm25Retriever(db, embedder), questions, embedder.count, k=1)
    assert bm25.questions == 3 and bm25.hit == pytest.approx(2 / 3) and bm25.misses == ["q3"]
    assert bm25.mrr == pytest.approx(2 / 3)
    long = score_method(db, LongContext(db), [q("q1", "x", asins=["B0AAAAAAA1"])], embedder.count)
    assert long.recall == 1 and long.tokens > bm25.tokens
    assert "| bm25 | 3 | 0.67 | 0.67 | 0.67 |" in scores_table([bm25], 1)


def test_load_questions(tmp_path):
    path = tmp_path / "q.yaml"
    path.write_text("- id: q1\n  question: Is it quiet?\n  language: en\n  asins: [B0AAAAAAA1]\n"
                    "  gold:\n    - {asin: B0AAAAAAA1, ref: review 1, quote: loud}\n", encoding="utf-8")
    [one] = load_questions(path)
    assert one.gold[0].ref == "review 1" and one.asins == ["B0AAAAAAA1"] and one.category == "fridge"
