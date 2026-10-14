import pytest

from scout.picks.wishes import (Verdict, WishVerdict, WishVerdicts, judge_wish, judge_wishes, rank_by_wishes)
from scout.text.retrieve import Hit
from tests.test_picks_rank import card


def hit(listing_id, text="Product: X\nVery quiet", ref="review 1"):
    return Hit(100 + listing_id, listing_id, "review", ref, text, {}, 0.5)


def cards_with_scores(*scores):
    out = []
    for i, score in enumerate(scores, 1):
        c = card(i, 4.0, 100 * i)
        c.score = score
        out.append(c)
    return out


class Judge:
    """Gives the verdicts of each call in turn and keeps the prompts."""

    def __init__(self, *answers):
        self.answers, self.prompts = list(answers), []

    def structured(self, role, system, user, schema):
        assert role == "worker" and schema is WishVerdicts
        self.prompts.append(user)
        return WishVerdicts(verdicts=[WishVerdict(id=i, verdict=v, part=p, quote=q)
                                      for i, v, p, q in self.answers.pop(0)])


def test_without_wishes_the_rating_orders():
    ranked = rank_by_wishes(cards_with_scores(4.0, 4.5, 3.0), [], {})
    assert [r.card.listing_id for r in ranked] == [1, 2, 3]  # the order of rank() stays
    assert [r.score for r in ranked] == [pytest.approx(2 / 3), 1.0, 0.0]


def test_a_met_wish_moves_a_candidate_up_and_an_opposite_one_moves_it_down():
    cards = cards_with_scores(4.5, 4.4, 4.0)
    verdicts = {1: {"quiet": Verdict("against", hit(1, "Very loud"))}, 2: {"quiet": Verdict("meets", hit(2))}}
    ranked = rank_by_wishes(cards, ["quiet"], verdicts)
    assert [r.card.listing_id for r in ranked] == [2, 1, 3]
    assert ranked[0].moved_by() == ["quiet"] and ranked[1].against() == ["quiet"]
    assert ranked[2].verdicts == {"quiet": Verdict("unclear")}  # no verdict is unclear
    assert ranked[0].score == pytest.approx(0.5 * 0.8 + 0.5) and ranked[1].score == pytest.approx(0.5)


def test_price_is_not_in_the_score_and_missing_values_stay_last():
    cards = cards_with_scores(4.0, 4.0, 4.0)
    cards[0].price, cards[1].price = 1, 100000
    cards[1].missing = ["height"]
    verdicts = {1: {"quiet": Verdict("unclear")}, 2: {"quiet": Verdict("meets")}, 3: {"quiet": Verdict("meets")}}
    assert [r.card.listing_id for r in rank_by_wishes(cards, ["quiet"], verdicts)] == [3, 1, 2]


def test_cards_that_were_not_judged_come_after_the_judged_ones():
    cards = cards_with_scores(4.0, 4.9, 3.0)
    verdicts = {1: {"quiet": Verdict("against")}, 3: {"quiet": Verdict("unclear")}}
    assert [r.card.listing_id for r in rank_by_wishes(cards, ["quiet"], verdicts)] == [1, 3, 2]


def test_the_worker_judges_the_nearest_parts_and_a_left_out_product_is_unclear():
    llm = Judge([(1, "against", 2, " sehr  LAUT "), (2, "meets", 9, "Very quiet"), (7, "meets", 1, "Very quiet"),
                 (4, "meets", 1, "silent at night"), (5, "meets", 0, "")])
    items = {1: ("Fridge 1", [hit(1, "Nice door"), hit(1, "Sehr laut", "review 2")]),
             **{i: (f"Fridge {i}", [hit(i)]) for i in range(2, 6)}}
    out = judge_wish(llm, "tyst", items)
    assert out[1] == Verdict("against", items[1][1][1], "sehr  LAUT")  # Python finds the quote in its part
    assert out[2] == Verdict("unclear")  # a part number that does not exist
    assert out[4] == Verdict("unclear")  # words that are not in the part
    assert out[5] == Verdict("unclear")  # no part and no words
    assert out[3] == Verdict("unclear") and 7 not in out
    assert llm.prompts[0].startswith("The wish: tyst\n\nProduct id 1: Fridge 1\n[1] (review) Nice door\n[2]")


def test_judge_wishes_keeps_the_verdicts_of_the_session():
    cards = cards_with_scores(4.0, 4.0, 4.0)
    asked = []

    def matches(ids, wishes):
        asked.append((ids, wishes))
        return {i: [[hit(i)] for _ in wishes] for i in ids if i != 3}  # product 3 has no indexed text
    cache = {}
    llm = Judge([(1, "meets", 1, "Very quiet"), (2, "unclear", 0, "")])
    first = judge_wishes(llm, cards, ["quiet"], matches, cache)
    assert first[1]["quiet"].label == "meets" and first[3]["quiet"] == Verdict("unclear")
    again = judge_wishes(llm, cards[:2], ["quiet"], matches, cache)
    assert again == {1: first[1], 2: first[2]} and len(llm.prompts) == 1  # the same text is not judged again
    llm.answers.append([(1, "against", 1, "Loud")])
    newer = judge_wishes(llm, cards[:1], ["quiet"],
                         lambda ids, w: {1: [[Hit(999, 1, "review", "review 9", "Loud", {}, 0.5)]]}, cache)
    assert newer[1]["quiet"].label == "against" and len(llm.prompts) == 2  # new text is judged again
