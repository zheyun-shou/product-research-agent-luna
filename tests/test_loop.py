import copy
from decimal import Decimal

from langgraph.checkpoint.memory import InMemorySaver

from scout.agent.loop import (MAX_TOOL_CALLS, Claim, FindProducts, LoopDeps, Pick, ReadReviews, RemoveProducts,
                              Reply, SearchShop, ShowPicks, UpdateNeeds, build_agent_graph)
from scout.needs import ConditionSpec
from scout.profiles import load_profile
from tests.fakes import FakeLLM
from tests.test_picks_rank import card

FRIDGE = load_profile("fridge")


class Shop:
    def __init__(self):
        self.cards = {i: card(i, 4.0 + i / 10, 100 * i, height=150.0 + i) for i in range(1, 5)}
        self.searched, self.checked, self.reviewed, self.removed, self.said = [], [], set(), set(), []
        self.problems = {}
        self.pages = 4  # product pages of each search

    def deps(self, llm):
        return LoopDeps(
            llm=llm, profile_names=lambda: ["fridge"], load_profile=lambda name: FRIDGE,
            cards=lambda ids: [copy.deepcopy(self.cards[i]) for i in ids if i in self.cards],
            find=lambda profile, needs, removed: [c for c in self.cards.values() if c.product_id not in removed],
            eliminate=lambda thread, ids, reason: self.removed.update(ids),
            eliminated=lambda thread: set(self.removed),
            restore=lambda thread, ids: self.removed.difference_update(ids),
            search_shop=self.search, check_now=self.check, read_reviews=self.read,
            reviews_read=lambda ids: {i for i in ids if i in self.reviewed}, say=self.said.append)

    def search(self, profile, needs, words, page):
        self.searched.append((words, page))
        return f"'{words}' page {page}: 48 results, 4 stored.", self.pages

    def check(self, ids, needs):
        self.checked.append(list(ids))
        return {i: self.problems.get(i, "") for i in ids}

    def read(self, ids):
        self.reviewed |= set(ids)
        return f"read the reviews of {len(ids)} products: 6 review pages.", 6


def run(graph, text, thread="t1"):
    return graph.invoke({"messages": [{"role": "user", "content": text}]}, {"configurable": {"thread_id": thread}})


NEEDS = UpdateNeeds(category="fridge", kind="fridge_freezer", conditions=[ConditionSpec(attribute="height", op="<=",
                                                                                         value=180)])
PICKS = ShowPicks(picks=[Pick(id=2, text="Quiet, 152 cm high.", claims=[Claim(id=2, field="height", value="152")]),
                         Pick(id=1, text="Fits the space.")])


def test_a_question_without_tools():
    llm = FakeLLM(["Which kind of fridge do you want?"])
    state = run(build_agent_graph(Shop().deps(llm), InMemorySaver()), "I want a fridge")
    assert state["messages"][-1]["content"] == "Which kind of fridge do you want?"
    assert state.get("picks", []) == []


def test_the_whole_way_to_the_picks():
    shop = Shop()
    llm = FakeLLM([[NEEDS, SearchShop(words="kyl och frys")], [FindProducts()], [ReadReviews(ids=[2, 1])], [PICKS]])
    state = run(build_agent_graph(shop.deps(llm), InMemorySaver()), "A fridge with a freezer, at most 180 cm")
    assert shop.searched == [("kyl och frys", 1)] and shop.checked == [[2, 1]]
    assert [p["listing_id"] for p in state["picks"]] == [2, 1]
    text = state["messages"][-1]["content"]
    assert text.startswith("1. Fridge 2") and "Quiet, 152 cm high." in text
    assert state["searches"]["kyl och frys"]["page"] == 1
    assert "id 2: Fridge 2" in llm.requests[2][1]  # the results of find_products reach the model
    assert shop.said[0].startswith("Searching amazon.se for 'kyl och frys'")


def test_the_floors_send_the_picks_back():
    shop = Shop()
    llm = FakeLLM([[NEEDS], [PICKS], [SearchShop(words="kylskåp"), ReadReviews(ids=[1, 2])], [PICKS]])
    state = run(build_agent_graph(shop.deps(llm), InMemorySaver()), "A fridge at most 180 cm")
    retry = llm.requests[2][1]
    assert "no shop search in this conversation yet" in retry and "the reviews of id 2 were not read" in retry
    assert [p["listing_id"] for p in state["picks"]] == [2, 1]


def test_a_pick_that_is_sold_out_now_goes_back():
    shop = Shop()
    shop.problems = {2: "sold out now"}
    shop.reviewed = {1, 2, 3}
    llm = FakeLLM([[NEEDS, SearchShop(words="kylskåp")], [PICKS],
                   [ShowPicks(picks=[Pick(id=1, text="Fits."), Pick(id=3, text="Fits too.")])]])
    state = run(build_agent_graph(shop.deps(llm), InMemorySaver()), "A fridge at most 180 cm")
    assert "id 2: sold out now" in llm.requests[2][1]
    assert [p["listing_id"] for p in state["picks"]] == [1, 3] and shop.checked == [[2, 1], [3]]


def test_a_number_without_a_source_goes_back():
    shop = Shop()
    llm = FakeLLM([[Reply(text="Fridge 3 is 199 cm high.")],
                   [Reply(text="Fridge 3 is 153 cm high.", claims=[Claim(id=3, field="height", value="153")])]])
    state = run(build_agent_graph(shop.deps(llm), InMemorySaver()), "How tall is fridge 3?")
    assert "the number 199 has no source" in llm.requests[1][1]
    assert state["messages"][-1]["content"] == "Fridge 3 is 153 cm high."


def test_numbers_from_tool_results_and_the_shopper_need_no_claim():
    shop = Shop()
    llm = FakeLLM([[NEEDS, SearchShop(words="kylskåp")], "The search found 48 results; you said 180 cm."])
    state = run(build_agent_graph(shop.deps(llm), InMemorySaver()), "Search fridges up to 180 cm")
    assert state["messages"][-1]["content"] == "The search found 48 results; you said 180 cm."


def test_needs_with_a_number_that_the_shopper_did_not_write_are_not_recorded():
    llm = FakeLLM([[UpdateNeeds(category="fridge", conditions=[ConditionSpec(attribute="height", op="<=",
                                                                             value=175)])], "Ok."])
    state = run(build_agent_graph(Shop().deps(llm), InMemorySaver()), "A fridge up to 180 cm")
    assert "not recorded" in llm.requests[1][1] and not state.get("needs")


def test_remove_products_and_the_picks_panel():
    shop = Shop()
    graph = build_agent_graph(shop.deps(llm := FakeLLM([
        [NEEDS, SearchShop(words="kylskåp"), ReadReviews(ids=[1, 2, 3])], [PICKS],
        [RemoveProducts(ids=[2], reason="too loud")], "Removed it."])), InMemorySaver())
    run(graph, "A fridge at most 180 cm")
    state = run(graph, "Not the first one")
    assert shop.removed == {2} and [p["listing_id"] for p in state["picks"]] == [1]
    assert state["removed"][0]["listing_id"] == 2
    assert "Removed in this conversation: id 2" in llm.requests[3][1]


def test_the_product_pages_of_a_turn_have_a_limit():
    shop = Shop()
    llm = FakeLLM([[NEEDS], *[[SearchShop(words=f"kyl {i}")] for i in range(9)], "Enough."])
    run(build_agent_graph(shop.deps(llm), InMemorySaver()), "A fridge up to 180 cm")
    assert len(shop.searched) == 8  # 8 searches of 4 pages pass 30
    assert "product pages of this turn are used up" in llm.requests[-1][1]


def test_the_tool_calls_of_a_turn_have_a_limit():
    shop = Shop()
    shop.pages = 0
    many = [[SearchShop(words=f"kyl {i}")] for i in range(MAX_TOOL_CALLS + 2)]
    llm = FakeLLM([[NEEDS], *many])
    state = run(build_agent_graph(shop.deps(llm), InMemorySaver()), "A fridge up to 180 cm")
    assert len(shop.searched) == MAX_TOOL_CALLS - 1
    assert "could not finish this within the limits" in state["messages"][-1]["content"]
    assert "tool calls of this turn are used up" in llm.requests[-1][1]


def test_each_turn_starts_with_an_empty_log():
    shop = Shop()
    llm = FakeLLM([[SearchShop(words="kylskåp")], "Done.", "Hello again."])
    graph = build_agent_graph(shop.deps(llm), InMemorySaver())
    run(graph, "Search")
    state = run(graph, "Hi")
    assert state["turn_log"] == [] and "Tool results in this turn" not in llm.requests[-1][1]
