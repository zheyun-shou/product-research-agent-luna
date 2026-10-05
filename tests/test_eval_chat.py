from scout.agent.clarify import CategoryChoice, ClarifyReply
from scout.agent.followup import RemovePicks
from scout.config import ROOT
from scout.eval.chat import PICKS_DIFFER, Expect, Script, Turn, load_scripts, run_scripts, summarize
from scout.llm.client import LLMOutputError, QuotaExhausted
from scout.needs import Needs
from tests.fakes import FakeLLM
from tests.test_graph import World, ready


def test_a_script_is_scored_turn_by_turn():
    world = World()
    llm = FakeLLM([CategoryChoice(category="fridge"), ClarifyReply(needs=Needs(budget=9000), ready=False,
                                                                   question="Which kind?"),
                   ready(budget=9000), [RemovePicks(picks=[2], words="remove 2")]])
    script = Script(id="s", turns=[
        Turn(say="a fridge, budget 9000", expect=Expect(phase="clarify", budget=9000, searched=False)),
        Turn(say="with a freezer", expect=Expect(phase="picks", kind="fridge_freezer", conditions=["height<=186"])),
        Turn(say="remove 2", expect=Expect(route="exclude", tools=["remove_picks"], pick_numbers=[2], says="Removed",
                                           not_says="Which pick")),
    ])
    records = run_scripts([script], world.deps(llm), world.crawls, llm)
    assert [c["ok"] for c in records[0]["checks"]] == [True, True, True]
    assert [(c["check"], c["ok"]) for c in records[1]["checks"]] == [("phase", True), ("kind", True),
                                                                      ("conditions", False)]
    assert all(c["ok"] for c in records[2]["checks"])
    assert records[0]["steps"] == {"clarify": {"rounds": 1, "passed": True, "first_round": True}}
    summary = summarize(records)
    assert summary["checks"]["conditions"] == {"ok": 0, "total": 1} and summary["errors"] == 0
    assert summary["steps"]["clarify"] == {"ran": 2, "first_round": 2, "passed": 2}


def test_the_scripts_load():
    scripts = load_scripts(ROOT / "eval" / "chat" / "conversations.yaml")
    assert len(scripts) == 24 and len({s.id for s in scripts}) == 24


def test_a_turn_that_a_node_ended_counts_as_an_error():
    world = World()
    llm = FakeLLM([QuotaExhausted("The free daily quota is used up.")])
    script = Script(id="s", turns=[Turn(say="a fridge", expect=Expect(phase="clarify"))])
    records = run_scripts([script], world.deps(llm), world.crawls, llm)
    assert records[0]["error"].startswith("I cannot finish this step now") and not records[0]["measured"]
    summary = summarize(records)
    assert summary["errors"] == 1 and summary["not_measured"] == 1
    assert summary["checks"] == {}  # an outage does not lower the score of the model


def test_a_step_that_ends_without_a_passing_round_did_not_pass():
    world = World()
    llm = FakeLLM([CategoryChoice(category="fridge"), ready(),
                   [RemovePicks(picks=[9], words="remove 9")], LLMOutputError("no valid tool calls")])
    script = Script(id="s", turns=[Turn(say="a fridge with a freezer"), Turn(say="remove 9")])
    records = run_scripts([script], world.deps(llm), world.crawls, llm)
    assert records[1]["error"] is None and records[1]["measured"]  # a model failure is measured
    assert records[1]["steps"]["actions"] == {"rounds": 1, "passed": False, "first_round": False}
    assert summarize(records)["steps"]["actions"] == {"ran": 1, "first_round": 0, "passed": 0}


def test_when_the_picks_differ_the_later_turns_are_not_measured():
    world = World()
    llm = FakeLLM([CategoryChoice(category="fridge"), ready(), [RemovePicks(picks=[1], words="remove 1")]])
    script = Script(id="s", turns=[
        Turn(say="a fridge with a freezer", expect=Expect(phase="picks", picks=["Fridge 2", "Fridge 1"])),
        Turn(say="remove 1", expect=Expect(pick_numbers=[1])),
    ])
    ends = []
    records = run_scripts([script], world.deps(llm), world.crawls, llm, after_turn=lambda: ends.append(1))
    assert records[0]["checks"][1] == {"check": "picks", "expected": ["Fridge 2", "Fridge 1"],
                                       "actual": ["Fridge 1", "Fridge 2"], "ok": False}
    assert records[0]["measured"] and records[1]["not_measured"] == PICKS_DIFFER
    summary = summarize(records)
    assert summary["checks"] == {"phase": {"ok": 1, "total": 1}, "picks": {"ok": 0, "total": 1}}
    assert summary["not_measured"] == 1 and len(ends) == 2  # the transaction ends after each turn


def test_each_turn_keeps_the_rounds_and_why_python_rejected_them():
    world = World()
    llm = FakeLLM([CategoryChoice(category="fridge"), ready(),
                   [RemovePicks(picks=[9], words="remove 9")],
                   [RemovePicks(picks=[2], words="remove 2")]])
    script = Script(id="s", turns=[Turn(say="a fridge with a freezer"), Turn(say="remove 9, no, remove 2")])
    records = run_scripts([script], world.deps(llm), world.crawls, llm)
    rounds = [r for r in records[1]["rounds"] if r["schema"] == "Act"]
    assert [r["passed"] for r in rounds] == [False, True]
    assert rounds[0]["problems"] == ["there is no pick 9"] and rounds[1]["problems"] == []
    assert '"picks": [9]' in rounds[0]["output"]
    assert records[0]["cards"] == [] and records[0]["new_cards"][0].startswith("Pick 1: Fridge 1 | price=")
    assert records[1]["cards"] == records[0]["new_cards"] and records[1]["actions"]["pick_numbers"] == [2]
