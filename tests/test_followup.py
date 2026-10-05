from decimal import Decimal

from scout.agent.followup import (AnswerClaim, NextStep, RemovePicks, Reply, RestorePicks, act, check_act,
                                  check_reply, says_done)
from tests.fakes import FakeLLM
from tests.test_picks_rank import card

PICKS = [{"listing_id": 1, "product_id": 1, "title": "Bomann KG 320"},
         {"listing_id": 2, "product_id": 2, "title": "Bomann SBS 7344"},
         {"listing_id": 3, "product_id": 3, "title": "FOHERE 90L"}]
CARDS = [card(1, 4.4, 1779, height=143.0, no_frost=True), card(2, 4.2, 54, height=177.0),
         card(3, 4.2, 54, height=48.0)]
REMOVED = [{"product_id": 9, "listing_id": 9, "title": "Severin RKG 8930"}]


def run_act(calls, last_user, removed=()):
    llm = FakeLLM(calls)
    return act(llm, PICKS, CARDS, list(removed), f"Shopper: {last_user}", set(), last_user), llm


def test_check_reply_allows_claims_the_shoppers_numbers_and_pick_numbers():
    good = Reply(text="Pick 1 is 143 cm high, under your 186.",
                 claims=[AnswerClaim(pick=1, field="height", value="143")])
    assert check_reply(good, CARDS, {Decimal("186")}) == []
    bad = Reply(text="Pick 3 is 80 cm high.", claims=[AnswerClaim(pick=3, field="height", value="80")])
    errors = check_reply(bad, CARDS, set())
    assert any("height is 48.0, not 80" in e for e in errors) and any("the number 80" in e for e in errors)
    assert check_reply(Reply(text="ok", claims=[AnswerClaim(pick=4, field="price", value="1")]), CARDS, set()) == [
        "there is no pick 4"]


def test_a_clear_removal_and_a_next_step_in_one_reply():
    result, llm = run_act([[RemovePicks(picks=[3], words="the FOHERE", one_pick=True, reason="too small"),
                            NextStep(step="show_more")]], "Drop the FOHERE and show me more")
    assert result.remove.picks == [3] and result.step == "show_more" and result.action == "more"
    assert result.question is None and result.tools == ["remove_picks", "next_step"]
    assert "Pick 3: Fridge 3 | price=5000 SEK" in llm.requests[0][1] and "height=48.0" in llm.requests[0][1]
    assert "product 9" not in llm.requests[0][1]


def test_one_pick_meant_but_several_fit_makes_python_ask():
    result, _ = run_act([[RemovePicks(picks=[2, 1], words="the Bomann", one_pick=True),
                          Reply(text="Removed the Bomann.")]], "Remove the Bomann")
    assert result.action == "stop"
    assert result.question == ("Which pick do you mean: pick 1 (Bomann KG 320), pick 2 (Bomann SBS 7344)? "
                               "Give its number.")
    result, _ = run_act([[RemovePicks(picks=[], words="that one", one_pick=True)]], "Remove that one")
    assert result.question.startswith("Which pick do you want to remove?")


def test_several_picks_can_go_when_the_shopper_means_several():
    result, _ = run_act([[RemovePicks(picks=[1, 2], words="both Bomanns", one_pick=False)]], "Remove both Bomanns")
    assert result.remove.picks == [1, 2] and result.question is None and result.action == "exclude"


def test_a_reply_that_claims_a_removal_without_the_tool_goes_back():
    errors = check_act([Reply(text="Removed pick 3.")], CARDS, set(), set(), "remove that one")
    assert any("you did not call remove_picks" in e for e in errors)
    assert check_act([Reply(text="Which pick should be removed?")], CARDS, set(), set(), "remove it") == []
    assert says_done("Done. I removed pick 2.") and not says_done("Do you want pick 2 removed?")


def test_words_must_come_from_the_shopper_and_picks_must_exist():
    errors = check_act([RemovePicks(picks=[4], words="the loud one", one_pick=True)], CARDS, set(), set(),
                       "Remove the noisy one")
    assert "there is no pick 4" in errors
    assert any("the words 'the loud one' are not in the shopper's last message" in e for e in errors)
    assert check_act([RemovePicks(picks=[2], words="第二个", one_pick=True)], CARDS, set(), set(), "第二个我不要") == []
    assert check_act([NextStep(step="recheck"), NextStep(step="show_more")], CARDS, set(), set(), "x") == [
        "call next_step only once"]


def test_restore_takes_only_removed_products():
    assert check_act([RestorePicks(product_ids=[5])], CARDS, {9}, set(), "bring it back") == [
        "product 5 was not removed in this conversation"]
    result, llm = run_act([[RestorePicks(product_ids=[9])]], "Bring back the Severin", REMOVED)
    assert result.action == "restore" and "product 9: Severin RKG 8930" in llm.requests[0][1]


def test_act_retries_with_the_problems_and_gives_up_after_three_rounds():
    bad = [Reply(text="Pick 3 is 80 cm high.")]
    good = [Reply(text="Pick 3 is 48 cm high.", claims=[AnswerClaim(pick=3, field="height", value="48")])]
    result, llm = run_act([bad, good], "How tall is 3?")
    assert result.reply == good[0] and result.action == "answer"
    assert "the number 80" in llm.requests[1][1] and '"tool":"reply"' in llm.requests[1][1]
    assert [c.schema for c in llm.calls] == ["Act", "Act"]
    assert [c.checked for c in llm.calls] == [False, True]  # the evaluation reads the result from the records
    assert run_act([bad] * 3, "How tall?")[0] is None
