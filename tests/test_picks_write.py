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
