from scout.agent.clarify import CategoryChoice, ClarifyReply
from scout.agent.followup import RemovePicks
from scout.config import ROOT
from scout.eval.chat import Expect, Script, Turn, load_scripts, run_scripts, summarize
from scout.llm.client import QuotaExhausted
from scout.needs import Needs
from tests.fakes import FakeLLM
from tests.test_graph import World, ready


def test_a_script_is_scored_turn_by_turn():
    world = World()
    llm = FakeLLM([CategoryChoice(category="fridge"), ClarifyReply(needs=Needs(budget=9000), ready=False,
                                                                   question="Which kind?"),
                   ready(budget=9000), [RemovePicks(picks=[2], words="remove 2", one_pick=True)]])
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
    assert records[0]["error"].startswith("I cannot finish this step now")
    assert summarize(records)["errors"] == 1
