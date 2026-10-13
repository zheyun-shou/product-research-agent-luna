import pytest

from scout.eval.make_questions import GoldOut, QuestionOut, QuestionSet, Source, checked, make_questions
from tests.test_retrieve import corpus  # noqa: F401  (fixture)


def source(number, text, asin="B0AAAAAAA1", ref="review 1"):
    return Source(number, asin, ref, "review", text)


def test_only_gold_labels_with_their_words_in_the_chunk_are_kept():
    items = [source(1, "The compressor is loud at night"), source(2, "Nice white door", ref="review 2")]
    out = QuestionOut(question="Is it loud?", gold=[GoldOut(chunk=1, quote="compressor is loud"),
                                                     GoldOut(chunk=1, quote=" loud at night "),
                                                     GoldOut(chunk=2, quote="red door"), GoldOut(chunk=9, quote="x")])
    gold, problems = checked(out, items)
    assert gold == [{"asin": "B0AAAAAAA1", "ref": "review 1", "quote": "compressor is loud"}]
    assert problems == ["the words 'red door' are not in chunk [2]", "no chunk [9]"]


class Writer:
    """Gives one question set for each call and keeps the prompts."""

    def __init__(self, sets):
        self.sets, self.prompts = list(sets), []

    def structured(self, role, system, user, schema):
        self.prompts.append(user)
        return self.sets.pop(0)


@pytest.mark.db
def test_questions_about_one_product_and_across_products(corpus):
    db, *_ = corpus
    llm = Writer([
        QuestionSet(questions=[QuestionOut(question="Is the RB38C7B5D22 loud?",
                                           gold=[GoldOut(chunk=1, quote="compressor is loud")])]),
        QuestionSet(questions=[QuestionOut(question="Which one is 203 cm?",
                                           gold=[GoldOut(chunk=99, quote="203 cm")])]),
    ])
    made = make_questions(db, llm, one=1, per_product=1, across=0, min_chunks=3, say=lambda text: None)
    assert made.asked == 1 and len(made.questions) == 1
    q = made.questions[0]
    assert q["id"] == "q01" and q["language"] == "sv" and q["style"] == "model" and q["asins"] == []
    assert q["gold"] == [{"asin": "B0AAAAAAA1", "ref": "review 1", "quote": "compressor is loud"}]
    assert "[1] (this product, review)" in llm.prompts[0] and "Swedish" in llm.prompts[0]
