from decimal import Decimal

from scout.agent.followup import (AnswerClaim, Derived, NextStep, RemovePicks, Reply, RestorePicks, act, check_act,
                                  check_reply, unbacked_actions)
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
    result, llm = run_act([[RemovePicks(picks=[3], words="the FOHERE", reason="too small"),
                            NextStep(step="show_more")]], "Drop the FOHERE and show me more")
    assert result.remove.picks == [3] and result.step == "show_more" and result.action == "more"
    assert result.question is None and result.tools == ["remove_picks", "next_step"]
    assert "Pick 3: Fridge 3 | price=5000 SEK" in llm.requests[0][1] and "height=48.0" in llm.requests[0][1]
    assert "product 9" not in llm.requests[0][1]


def test_several_picks_without_numbers_make_python_ask():
    result, _ = run_act([[RemovePicks(picks=[2, 1], words="the Bomann"), Reply(text="Removed the Bomann.")]],
                        "Remove the Bomann")
    assert result.action == "stop"
    assert result.question == ("Which do you mean, all of these or only one: pick 1 (Bomann KG 320), pick 2 "
                               "(Bomann SBS 7344)? Say all, or give the number.")
    assert run_act([[RemovePicks(picks=[1, 3], words="红色那个")]], "去掉红色那个")[0].action == "stop"
    result, _ = run_act([[RemovePicks(picks=[], words="that one")]], "Remove that one")
    assert result.question.startswith("Which pick do you want to remove?")


def test_several_picks_can_go_when_the_shopper_gives_each_number_or_says_all():
    for words, said in [("both Bomanns", "Remove both Bomanns"), ("1 and 2", "Remove 1 and 2"),
                        ("红色的", "去掉所有红色的"), ("all", "all"), ("the red ones", "Remove the red ones"),
                        ("超声波的", "超声波的不要，最好一次能洗15个碗及以上的")]:
        result, _ = run_act([[RemovePicks(picks=[1, 2], words=words)]], said)
        assert result.remove.picks == [1, 2] and result.question is None and result.action == "exclude", said
    result, _ = run_act([[RemovePicks(picks=[1], words="the red one")]], "Remove the red one")
    assert result.question is None and result.action == "exclude"


def test_a_reply_that_claims_a_removal_without_the_tool_goes_back():
    errors = check_act([Reply(text="Removed pick 3.")], CARDS, set(), set(), "remove that one")
    assert any("the reply says 'Removed'" in e for e in errors)
    assert check_act([Reply(text="Which pick should be removed?")], CARDS, set(), set(), "remove it") == []
    assert unbacked_actions("Done. I removed pick 2.", set()) == ["removed"]
    assert unbacked_actions("Do you want pick 2 removed?", set()) == []


def test_a_reply_that_promises_an_action_needs_the_call_now():
    cases = [
        ("I'll recheck the prices now.", "recheck", {"next_step:recheck"}),
        ("Let me search for more kettles.", "search for more", {"next_step:show_more"}),
        ("I will remove the Bomann.", "remove", {"remove_picks"}),
        ("I rechecked the stock.", "I rechecked", {"next_step:recheck"}),
        ("Sure, I'll bring back the Severin.", "bring back", {"restore_picks"}),
        # Luna, 2026-10-05: the curly apostrophe and the present tense. The grader found it, Python did not.
        ("I’m rechecking the current prices and stock.", "rechecking", {"next_step:recheck"}),
        ("I’ll recheck them.", "recheck", {"next_step:recheck"}),
    ]
    for text, words, done in cases:
        assert unbacked_actions(text, set()) == [words], text
        assert unbacked_actions(text, done) == [], text
    for text in ["Do you want me to recheck the prices?", "I cannot recheck now. The prices are from 3 October.",
                 "If you want, I'll search for more.", "The price dropped on 3 October.",
                 "The prices were rechecked on 3 October.", "Let me explain: pick 1 is the tallest.",
                 "I’m happy to help.", "I’m not able to recheck the prices now."]:
        assert unbacked_actions(text, set()) == [], text
    errors = check_act([Reply(text="I'll recheck the prices.")], CARDS, set(), set(), "are they still in stock")
    assert errors == ["the reply says 'recheck', but no tool call in this reply does it. Nothing runs after your "
                      "reply. Call the tool in this reply, or change the text"]
    assert check_act([NextStep(step="recheck"), Reply(text="I'll recheck the prices.")], CARDS, set(), set(),
                     "are they still in stock") == []


def test_picks_must_exist_and_the_words_are_only_a_record():
    errors = check_act([RemovePicks(picks=[4], words="the loud one")], CARDS, set(), set(),
                       "Remove the noisy one")
    assert errors == ["there is no pick 4"]  # "loud" for "noisy" is fine
    assert check_act([RemovePicks(picks=[2], words="第二个")], CARDS, set(), set(), "第二个我不要") == []
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


def test_the_model_can_calculate_and_facts_are_still_checked():
    with_shipping = Reply(text="Pick 1 costs 5000 SEK, so with the 499 SEK shipping it is 5499 SEK.",
                          claims=[AnswerClaim(pick=1, field="price", value="5000")],
                          derived=[Derived(value="5499", how="pick 1 price plus the 499 SEK shipping")])
    assert check_reply(with_shipping, CARDS, {Decimal("499")}) == []
    no_derived = with_shipping.model_copy(update={"derived": []})
    assert [e[:22] for e in check_reply(no_derived, CARDS, {Decimal("499")})] == ["the number 5499 is not"]
    wrong_fact = with_shipping.model_copy(update={"claims": [AnswerClaim(pick=1, field="price", value="4000")]})
    assert any("pick 1: price is 5000" in e for e in check_reply(wrong_fact, CARDS, {Decimal("499")}))


def test_the_model_can_answer_in_text_and_card_numbers_need_no_claim():
    result, llm = run_act(["Pick 1 is 143 cm high and costs 5000 SEK."], "How tall is 1?")
    assert result.action == "answer" and result.reply.text == "Pick 1 is 143 cm high and costs 5000 SEK."
    result, llm = run_act(["Pick 1 is 199 cm high.", "Pick 1 is 143 cm high."], "How tall is 1?")
    assert "the number 199 is not on the cards" in llm.requests[1][1]
    result, _ = run_act(["I'll recheck the prices.", [NextStep(step="recheck")]], "Still right?")
    assert result.action == "recheck"
