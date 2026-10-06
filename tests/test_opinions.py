from dataclasses import dataclass

from scout.picks.opinions import (PRAISE_QUERY, PickOpinions, Point, check_opinions, pick_opinions,
                                  retrieve_reviews, write_opinions)
from tests.fakes import FakeLLM
from tests.test_picks_rank import card

CHUNKS = {11: "Review (2.0 of 5): Zu laut, 45 dB in der Nacht.", 12: "Review (5.0 of 5): Tyst och kall.",
          13: "Review (1.0 of 5): Kom trasig."}


@dataclass
class Hit:
    chunk_id: int
    text: str


def opinions(pros=(), complaints=()):
    return PickOpinions(pros=[Point(text=t, sources=s) for t, s in pros],
                        complaints=[Point(text=t, sources=s) for t, s in complaints])


def test_good_opinions_pass():
    good = opinions(pros=[("Most buyers say it is quiet and cold.", [12])],
                    complaints=[("A few say it is loud at night, about 45 dB.", [11])])
    assert check_opinions(good, CHUNKS) == []


def test_citations_and_numbers_are_checked():
    bad = opinions(pros=[("Quiet.", [])], complaints=[("Broke.", [99]), ("12 buyers say it is loud.", [11])])
    errors = check_opinions(bad, CHUNKS)
    assert len(errors) == 3
    assert "cites no review" in errors[0] and "[99]" in errors[1] and "'12'" in errors[2]


def test_write_opinions_retries_with_the_problems():
    llm = FakeLLM([opinions(complaints=[("Loud.", [99])]), opinions(complaints=[("Some say it is loud.", [11])])])
    result, why = write_opinions(llm, card(1, 4.4, 1782), CHUNKS, "Swedish")
    assert result.complaints[0].text == "Some say it is loud." and why == ""
    assert "cites [99]" in llm.requests[1][1]
    assert "4.4 of 5 from 1782 ratings" in llm.requests[0][1] and "[12] Review (5.0 of 5)" in llm.requests[0][1]


def test_retrieval_uses_each_complaint_type_and_the_praise():
    asked = []

    def search(query, k):
        asked.append(query)
        return [Hit(11, "Product: Fridge 1\nReview: loud"), Hit(12, "Product: Fridge 1\nReview: quiet")]
    chunks = retrieve_reviews(card(1), ["noise", "frost"], search)
    assert asked == ["noise", "frost", PRAISE_QUERY]
    assert chunks == {11: "Review: loud", 12: "Review: quiet"}  # no repeats, no product line


def test_pick_opinions_text_and_stored_sources():
    cards = [card(1, 4.4, 100), card(2, 4.0, 2)]
    hits = {1: [Hit(i, f"x\n{t}") for i, t in CHUNKS.items()], 2: [Hit(21, "x\nOk")]}
    llm = FakeLLM([opinions(pros=[("Most say it is quiet.", [12])], complaints=[("A few got it broken.", [13])])])
    text, stored = pick_opinions(llm, list(enumerate(cards, 1)), ["noise"], lambda c: (lambda q, k: hits[c.listing_id]))
    assert text.splitlines() == ["What buyers say (buyer reviews from amazon.se):", "1. Fridge 1",
                                 "   + Most say it is quiet.", "   - A few got it broken.", "2. Fridge 2",
                                 "   Too few reviews to summarize."]
    assert stored == {1: {"pros": [{"text": "Most say it is quiet.", "sources": [12]}],
                          "complaints": [{"text": "A few got it broken.", "sources": [13]}]}}
    assert len(llm.requests) == 1


def test_why_there_is_no_summary():
    from scout.llm.client import LLMOutputError
    llm = FakeLLM([LLMOutputError("bad json")] * 3)
    assert write_opinions(llm, card(1), CHUNKS) == (None, "format")
    llm = FakeLLM([opinions(complaints=[("Loud.", [99])])] * 3)
    assert write_opinions(llm, card(1), CHUNKS) == (None, "checks")


def test_rating_without_a_count():
    from scout.picks.opinions import build_prompt
    assert "4.3 of 5 (the number of ratings is not known)" in build_prompt(card(1, 4.3, None), CHUNKS)


def test_an_outage_keeps_the_summaries_written_before():
    from scout.llm.client import QuotaExhausted
    hits = [Hit(i, f"x\n{t}") for i, t in CHUNKS.items()]
    llm = FakeLLM([opinions(pros=[("Most say it is quiet.", [12])]), QuotaExhausted("daily quota used up")])
    text, stored = pick_opinions(llm, [(1, card(1)), (3, card(3))], ["noise"], lambda c: (lambda q, k: hits))
    assert list(stored) == [1]
    assert "1. Fridge 1" in text and "3. Fridge 3" not in text and "The other picks have no summary now" in text


def test_no_text_when_no_pick_has_reviews():
    text, stored = pick_opinions(FakeLLM([]), [(1, card(1))], ["noise"], lambda c: (lambda q, k: []))
    assert (text, stored) == ("", {})


def test_numbers_are_those_that_the_shopper_saw():
    hits = [Hit(i, f"x\n{t}") for i, t in CHUNKS.items()]
    llm = FakeLLM([opinions(pros=[("Quiet, most say.", [12])])])
    text, _ = pick_opinions(llm, [(3, card(7))], ["noise"], lambda c: (lambda q, k: hits))
    assert "3. Fridge 7" in text
