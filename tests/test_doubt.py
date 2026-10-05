from scout.extract.doubt import SizeCheck, SizeDoubt, check_sizes
from scout.picks.write import format_picks
from scout.sources.base import ProductRecord
from tests.fakes import NOW, FakeLLM
from tests.test_picks_rank import card


def fohere():
    c = card(5, 4.2, 54, height=48.0, width=45.0, depth=80.0, total_volume=90.0)
    c.title = "FOHERE Kylskåp med frys 90L"
    rec = ProductRecord(source="amazon_se", source_id="B0F9Y2XN3J", url=c.url, fetched_at=NOW, title=c.title,
                        bullets=["Storlek: 48 x 45 x 80 cm (B x D x H)"])
    return c, rec


def test_a_doubt_with_a_quote_from_the_page_marks_the_sizes():
    c, rec = fohere()
    llm = FakeLLM([SizeCheck(doubts=[SizeDoubt(product=1, sizes=["height", "depth"],
                                               reason="height and depth look swapped",
                                               evidence="48 x 45 x 80 cm (B x D x H)")])])
    notes = check_sizes([(c, rec)], llm)
    assert c.attributes["height"] == {"value": 48.0, "doubtful": True}
    assert 'the page says "48 x 45 x 80 cm (B x D x H)"' in notes[5]
    assert "height: 48.0 (doubtful)" in format_picks([c], None, [], "", 0)
    assert llm.requests[0][0] == "worker"


def test_a_quote_that_is_not_on_the_page_is_ignored():
    c, rec = fohere()
    llm = FakeLLM([SizeCheck(doubts=[SizeDoubt(product=1, sizes=["height"], reason="x", evidence="Höjd: 80 cm")])])
    assert check_sizes([(c, rec)], llm) == {}
    assert "doubtful" not in c.attributes["height"]


def test_a_doubt_without_a_quote_is_the_worker_models_judgement():
    c, rec = fohere()
    llm = FakeLLM([SizeCheck(doubts=[SizeDoubt(product=1, sizes=["height"],
                                               reason="a 90 l fridge with a freezer part is taller than 48 cm")])])
    assert "judged by the worker model" in check_sizes([(c, rec)], llm)[5]


def test_cards_without_sizes_need_no_call():
    assert check_sizes([(card(1), fohere()[1])], FakeLLM([])) == {}
