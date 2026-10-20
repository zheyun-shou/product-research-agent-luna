import threading

from langgraph.checkpoint.memory import InMemorySaver

from scout.agent.loop import (Pick, ReadProduct, ResearchCandidates, SearchWeb, ShowPicks, build_agent_graph)
from scout.agent.researcher import FilePoint, ResearchFile, scoped
from tests.fakes import FakeLLM
from tests.test_loop import NEEDS
from tests.test_websearch import WebShop

PAGE = "Fridge 2 page. Noise 38 dB. Height 152 cm. Energy class E."


class Router:
    """The main agent gets its own queue; each researcher gets the steps of its product, in order."""

    def __init__(self, main, steps):
        self.main, self.steps, self.roles = FakeLLM(main), {k: list(v) for k, v in steps.items()}, []
        self.requests = self.main.requests
        self.lock = threading.Lock()

    def tools(self, role, system, user, tools, name="tools", text_reply=None, history=()):
        if name != "Researcher":
            return self.main.tools(role, system, user, tools, name, text_reply, history)
        with self.lock:
            self.roles.append(role)
            product = int(user.split("Your product: id ", 1)[1].split(",", 1)[0])
            reply = self.steps[product].pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class Shop(WebShop):
    def __init__(self):
        super().__init__()
        self.reviews_asked = []

    def deps(self, llm):
        deps = super().deps(llm)
        deps.page_texts = lambda ids: {i: {"description": PAGE} for i in ids}
        read = deps.read_reviews

        def reviews(ids):
            self.reviews_asked.append(list(ids))
            return read(ids)
        deps.read_reviews = reviews
        return deps


def run(llm, shop):
    return build_agent_graph(shop.deps(llm), InMemorySaver()).invoke(
        {"messages": [{"role": "user", "content": "A quiet fridge, at most 180 cm"}], "searches": {"x": {"page": 1}}},
        {"configurable": {"thread_id": "t"}})


def file_of(fit, *points):
    return ResearchFile(fit=fit, fit_reason="Height 152 cm.", points=list(points), missing="no test of the noise")


def test_scoped_sets_the_own_product():
    assert scoped(ReadProduct(ids=[1, 2, 3], question="q"), 2).ids == [2]
    assert scoped(SearchWeb(question="q", ids=[]), 2).ids == [2]


def test_each_candidate_gets_a_researcher_and_the_main_agent_a_short_file():
    shop = Shop()
    good = FilePoint(topic="need", text="38 dB.", source="page", quote="Noise 38 dB")
    made_up = FilePoint(topic="pro", text="Very quiet, 30 dB.", source="page", quote="Noise 30 dB")
    steps = {
        2: [[ReadProduct(ids=[2], question="noise"), SearchWeb(question="Fridge 2 test", ids=[])],
            [file_of("fits", good, made_up)]],
        1: [[SearchWeb(question="Fridge 1 test", ids=[5])], [file_of("unclear")]],
    }
    picks = ShowPicks(picks=[Pick(id=2, text="Quiet, 38 dB."), Pick(id=1, text="Fits.")])
    llm = Router([[NEEDS, ResearchCandidates(ids=[2, 1, 2, 9], want="is it quiet")], [picks]], steps)
    state = run(llm, shop)
    assert shop.reviews_asked == [[2, 1]]  # Python reads the reviews before the researchers start
    # Each search is for the own product of its researcher, with half of the pages of the turn
    assert sorted(shop.web_calls) == [("Fridge 1 test", [1], 60), ("Fridge 2 test", [2], 60)]
    result = llm.requests[1][1]
    assert "There is no product with id 9." in result
    assert '[id 2] Fridge 2\nFit: fits. Height 152 cm.\n- need: 38 dB. "Noise 38 dB" (page)' in result
    assert "30 dB" not in result and "(1 point(s) left out" in result
    assert "[id 1] Fridge 1\nFit: unclear." in result
    assert [p["listing_id"] for p in state["picks"]] == [2, 1]  # the reviews and web floors are met
    used = state["used"]
    assert used["calls"] == 2 and used["researcher_steps"] == 4 and used["web_searches"] == 2
    assert [(r["id"], r["kept"], r["dropped"]) for r in used["researchers"]] == [(2, 1, 1), (1, 0, 0)]
    assert set(llm.roles) == {"researcher"}


def test_a_researcher_that_fails_does_not_lose_the_others():
    from scout.llm.client import LLMUnavailable
    shop = Shop()
    steps = {2: [LLMUnavailable("busy")], 1: [[file_of("fits")]]}
    llm = Router([[NEEDS, ResearchCandidates(ids=[2, 1])], "Done."], steps)
    run(llm, shop)
    result = llm.requests[1][1]
    assert "[id 2] Fridge 2\n- not researched: the researcher model failed (LLMUnavailable: busy)" in result
    assert "[id 1] Fridge 1\nFit: fits." in result


def test_the_last_step_can_only_finish():
    from scout.agent.researcher import MAX_STEPS
    shop = Shop()
    steps = {2: [[ReadProduct(ids=[2], question="q")]] * (MAX_STEPS - 1) + [[file_of("unclear")]]}
    seen = []
    llm = Router([[NEEDS, ResearchCandidates(ids=[2])], "Done."], steps)
    tools = llm.tools

    def spy(role, system, user, tool_list, name="tools", text_reply=None, history=()):
        if name == "Researcher":
            seen.append([t.__name__ for t in tool_list])
        return tools(role, system, user, tool_list, name, text_reply, history)
    llm.tools = spy
    run(llm, shop)
    assert len(seen) == MAX_STEPS and seen[-1] == ["ResearchFile"] and "ReadProduct" in seen[0]


def test_a_point_cannot_quote_the_needs_or_the_wish_and_errors_of_the_reviews_do_not_stop_the_research():
    shop = Shop()

    def broken(ids):
        raise RuntimeError("browser closed")
    wish = FilePoint(topic="pro", text="Quiet.", source="wish", quote="is it quiet")
    fact = FilePoint(topic="need", text="152 cm.", source="facts", quote="height 152.0")
    llm = Router([[NEEDS, ResearchCandidates(ids=[2], want="is it quiet")], "Done."],
                 {2: [[file_of("fits", wish, fact)]]})
    deps = shop.deps(llm)
    deps.read_reviews = broken
    build_agent_graph(deps, InMemorySaver()).invoke(
        {"messages": [{"role": "user", "content": "A quiet fridge, at most 180 cm"}]},
        {"configurable": {"thread_id": "t"}})
    result = llm.requests[1][1]
    assert "Reviews: not read (RuntimeError: browser closed). Call read_reviews later." in result
    assert '"height 152.0" (facts)' in result and "Quiet." not in result and "(1 point(s) left out" in result


def test_a_web_search_that_is_not_available_reaches_the_turn():
    shop = Shop()
    shop.down = "no credits"
    steps = {2: [[SearchWeb(question="Fridge 2 test")], [file_of("fits")]]}
    picks = ShowPicks(picks=[Pick(id=2, text="Fits.")])
    llm = Router([[NEEDS, ResearchCandidates(ids=[2])], [picks]], steps)
    state = run(llm, shop)
    assert state["used"]["web_unavailable"] == "no credits"
    assert [p["listing_id"] for p in state["picks"]] == [2]  # the web floor does not ask for it


def test_a_run_without_the_researchers_has_no_tool_and_no_prompt_line_for_them():
    from scout.agent.loop import system_prompt, work_tools
    assert "research_candidates with them" in system_prompt() and ResearchCandidates in work_tools()
    assert "research_candidates with them" not in system_prompt(False)
    assert ResearchCandidates not in work_tools(False) and "{" not in system_prompt(False)[:40]
