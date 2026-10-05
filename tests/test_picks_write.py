from scout.picks.write import Claim, PickText, PicksText, check_picks, format_picks, write_picks
from tests.fakes import FakeLLM
from tests.test_picks_rank import card


def cards():
    a = card(1, 4.5, 1234, height=178.0)
    a.model = "RB38C7B5D22"
    return {1: a}


def picks(summary, claims):
    return PicksText(picks=[PickText(listing_id=1, summary=summary,
                                     claims=[Claim(field=f, value=v) for f, v in claims])])


def test_correct_claims_pass():
    text = picks("Fits at 178 cm, rated 4.5 by 1 234 buyers, 5000 kr.",
                 [("height", "178"), ("rating", "4.5"), ("review_count", "1234"), ("price", "5000")])
    assert check_picks(text, cards()) == []


def test_wrong_claim_and_uncited_number_are_caught():
    text = picks("Rated 4.7, only 30 dB.", [("rating", "4.7")])
    errors = check_picks(text, cards())
    assert any("rating is 4.5" in e for e in errors)
    assert any("30" in e for e in errors)


def test_unknown_id_and_unknown_field_are_caught():
    text = PicksText(picks=[PickText(listing_id=9, summary="Good.", claims=[]),
                            PickText(listing_id=1, summary="Quiet.", claims=[Claim(field="noise", value="35")])])
    errors = check_picks(text, cards())
    assert any("unknown listing_id 9" in e for e in errors)
    assert any("no field 'noise'" in e for e in errors)


def test_model_number_in_the_summary_is_not_treated_as_numbers():
    assert check_picks(picks("The RB38C7B5D22 is a solid choice.", []), cards()) == []


def test_the_rating_scale_is_not_a_claim():
    text = picks("Rated 4.5/5, that is 4.5 out of 5.", [("rating", "4.5")])
    assert check_picks(text, cards()) == []


def test_write_picks_retries_with_the_errors_then_succeeds():
    bad = picks("Rated 4.9.", [("rating", "4.9")])
    good = picks("Rated 4.5.", [("rating", "4.5")])
    llm = FakeLLM([bad, good])
    text, errors = write_picks(llm, list(cards().values()), "height<=180")
    assert text == good and errors == []
    assert llm.requests[0][0] == "chat"
    assert "rating is 4.5" in llm.requests[1][1]


def test_write_picks_gives_up_after_three_rounds():
    bad = picks("Rated 4.9.", [("rating", "4.9")])
    text, errors = write_picks(FakeLLM([bad, bad, bad]), list(cards().values()), "")
    assert text is None and errors


def test_format_shows_missing_data_and_how_ranking_works():
    c = cards()[1]
    c.missing = ["width"]
    out = format_picks([c], None, ["note one"], "width<=60", 2)
    assert "Fridge 1" in out and "width: missing" in out
    assert "Bayesian" in out and "note one" in out and "2 products failed" in out


def test_failed_checks_are_reported_only_when_summaries_were_written():
    c = cards()[1]
    assert "did not pass" not in format_picks([c], None, [], "", 0)
    out = format_picks([c], None, [], "", 0, check_errors=["1: rating is 4.5, not 4.9"])
    assert "did not pass" in out and "rating is 4.5, not 4.9" in out


def test_yes_no_and_class_claims_are_checked_as_text():
    c = card(1, 4.5, 1234, no_frost=True, energy_class="E")
    assert check_picks(picks("No frost, class E.", [("no_frost", "True"), ("energy_class", "E")]), {1: c}) == []
    assert check_picks(picks("No frost.", [("no_frost", "yes")]), {1: c}) == []
    errors = check_picks(picks("Class D, no frost.", [("energy_class", "D"), ("no_frost", "false")]), {1: c})
    assert any("energy_class is E, not D" in e for e in errors)
    assert any("no_frost is yes, not false" in e for e in errors)


def test_numbers_from_the_shoppers_conditions_may_be_repeated():
    text = picks("Under your 9 000 SEK budget and 180 cm limit.", [])
    assert check_picks(text, cards(), "height<=180, budget 9000 SEK") == []
    assert check_picks(text, cards()) != []


def test_the_next_round_gets_the_last_answer_and_the_numbers_on_the_card():
    bad = picks("Only 30 dB, rated 4.9.", [("rating", "4.9")])
    good = picks("Rated 4.5.", [("rating", "4.5")])
    llm = FakeLLM([bad, good])
    write_picks(llm, list(cards().values()), "")
    retry = llm.requests[1][1]
    assert '"summary":"Only 30 dB, rated 4.9."' in retry
    assert "rating is 4.5, not 4.9" in retry
    assert "the number 30 in the summary is not in the claims" in retry
    assert "Numbers on this card: price=5000, rating=4.5, review_count=1234, height=178" in retry


def test_after_the_last_round_only_failed_picks_lose_their_summary():
    two = {**cards(), 2: card(2, 4.0, 50)}
    text = PicksText(picks=[PickText(listing_id=1, summary="Rated 4.9.", claims=[Claim(field="rating", value="4.9")]),
                            PickText(listing_id=2, summary="Rated 4.0.", claims=[Claim(field="rating", value="4.0")])])
    kept, errors = write_picks(FakeLLM([text, text, text]), list(two.values()), "")
    assert [p.listing_id for p in kept.picks] == [2] and errors[0] == "1: rating is 4.5, not 4.9"
    assert all(e.startswith("1:") for e in errors)
    out = format_picks(list(two.values()), kept, [], "", 0, errors)
    assert "Rated 4.0." in out and "Rated 4.9." not in out and "Some summaries did not pass" in out
