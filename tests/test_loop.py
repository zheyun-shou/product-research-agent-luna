import copy

from langgraph.checkpoint.memory import InMemorySaver

from scout.agent.loop import (MAX_TOOL_CALLS, FindProducts, LoopDeps, Pick, ReadReviews, RemoveProducts,
                              SearchShop, ShowPicks, UpdateNeeds, build_agent_graph)
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
        self.unreadable = set()

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

    def search(self, profile, needs, words, page, max_details=20, filters=()):
        self.searched.append((words, page))
        self.filters = list(filters)
        self.max_details = max_details
        return f"'{words}' page {page}: 48 results, 4 stored.", self.pages

    def check(self, ids, needs):
        self.checked.append(list(ids))
        return {i: self.problems.get(i, "") for i in ids}

    def read(self, ids):
        self.reviewed |= set(ids)
        return f"read the reviews of {len(ids)} products: 6 review pages.", 6, list(self.unreadable & set(ids))


def run(graph, text, thread="t1"):
    return graph.invoke({"messages": [{"role": "user", "content": text}]}, {"configurable": {"thread_id": thread}})


NEEDS = UpdateNeeds(category="fridge", kind="fridge_freezer", conditions=[ConditionSpec(attribute="height", op="<=",
                                                                                         value=180)])
PICKS = ShowPicks(picks=[Pick(id=2, text="Quiet, 152 cm high."),
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
    assert "remove_products: removed 2." in llm.requests[3][1]  # the result of its own call, in the history


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
    assert llm.histories[-1] == [] and len(state["turn_messages"]) == 1  # only the reply of this turn


def test_the_last_search_gets_only_the_pages_that_are_left():
    shop = Shop()
    shop.pages = 12
    llm = FakeLLM([[NEEDS], [SearchShop(words="a")], [SearchShop(words="b")], [SearchShop(words="c")], "Ok."])
    run(build_agent_graph(shop.deps(llm), InMemorySaver()), "A fridge up to 180 cm")
    assert shop.max_details == 6  # 30 - 12 - 12


def test_reviews_that_cannot_be_read_do_not_block_the_picks():
    shop = Shop()
    shop.unreadable = {1}
    llm = FakeLLM([[NEEDS, SearchShop(words="kylskåp"), ReadReviews(ids=[2, 1])], [PICKS]])
    state = run(build_agent_graph(shop.deps(llm), InMemorySaver()), "A fridge at most 180 cm")
    assert [p["listing_id"] for p in state["picks"]] == [2, 1]


def test_a_pick_is_marked_checked_only_after_its_live_check_passed():
    shop = Shop()
    shop.reviewed = {1, 2}
    shop.problems = {2: "sold out now"}
    llm = FakeLLM([[NEEDS, SearchShop(words="kylskåp")], [PICKS], [PICKS], [PICKS]])
    state = run(build_agent_graph(shop.deps(llm), InMemorySaver()), "A fridge at most 180 cm")
    assert shop.checked == [[2, 1], [2], [2]]  # pick 2 is checked again each round; pick 1 passed the first time
    assert "could not finish the research" in state["messages"][-1]["content"]


def test_remove_then_remove_again_in_one_round():
    shop = Shop()
    shop.reviewed = {1, 2}
    llm = FakeLLM([[NEEDS, SearchShop(words="kylskåp")], [PICKS],
                   [RemoveProducts(ids=[1, 2]), RemoveProducts(ids=[3])], "Removed."])
    graph = build_agent_graph(shop.deps(llm), InMemorySaver())
    run(graph, "A fridge at most 180 cm")
    state = run(graph, "None of these, and not fridge 3")
    assert state["picks"] == [] and [r["listing_id"] for r in state["removed"]] == [1, 2, 3]


def page_shop():
    shop = Shop()
    shop.reviewed = {1, 2}
    rows = {1: {"specs": {"Mått": "55D x 49,5B x 142,5H centimeter"}, "bullets": [], "description": "",
                "reviews": [{"rating": 5, "text": "It is 99 cm wide"}]},
            2: {"specs": {"Höjd": "152 cm"}, "bullets": ["Long text " * 900], "description": ""}}
    shop.page_rows = rows
    return shop


def deps_with_pages(shop, llm):
    deps = shop.deps(llm)
    deps.page_texts = lambda ids: {i: shop.page_rows[i] for i in ids if i in shop.page_rows}
    shop.page_calls = []
    inner = deps.page_texts

    def counted(ids):
        shop.page_calls.append(list(ids))
        return inner(ids)
    deps.page_texts = counted
    return deps


def test_a_product_read_in_this_turn_is_not_given_again_and_long_texts_are_cut():
    from scout.agent.loop import MAX_READ_CHARS, ReadProduct
    shop = page_shop()
    llm = FakeLLM([[ReadProduct(ids=[1, 2], question="size")], [ReadProduct(ids=[2, 1], question="size")], "Ok."])
    run(build_agent_graph(deps_with_pages(shop, llm), InMemorySaver()), "How big are they?")
    assert shop.page_calls == [[1, 2]]
    first, second = llm.requests[1][1], llm.requests[2][1]
    assert "142,5H" in first and "[cut here" in first and len(first) < 4 * MAX_READ_CHARS
    assert "id 2 was read earlier in this turn" in second and "id 1 was read earlier in this turn" in second


def test_a_search_reads_at_most_8_product_pages():
    shop = Shop()
    llm = FakeLLM([[NEEDS, SearchShop(words="kylskåp")], "Ok."])
    run(build_agent_graph(shop.deps(llm), InMemorySaver()), "A fridge at most 180 cm")
    assert shop.max_details == 8


def answered(history):
    """Each tool call in the history has its result before the next call of the model, as the chat API needs."""
    open_ids = set()
    for message in history:
        if message["role"] == "assistant":
            assert not open_ids, open_ids
            open_ids = {c["id"] for c in message["tool_calls"]}
        elif message["role"] == "tool":
            open_ids.discard(message["tool_call_id"])
    return not open_ids


def test_the_model_sees_its_own_calls_and_their_results():
    shop = Shop()
    llm = FakeLLM([[NEEDS, SearchShop(words="kyl och frys")], [FindProducts()], [ReadReviews(ids=[2, 1])], [PICKS]])
    run(build_agent_graph(shop.deps(llm), InMemorySaver()), "A fridge with a freezer, at most 180 cm")
    first, third = llm.histories[0], llm.histories[2]
    assert first == []
    calls = third[0]["tool_calls"]
    assert [c["function"]["name"] for c in calls] == ["update_needs", "search_shop"]
    assert '"words": "kyl och frys"' in calls[1]["function"]["arguments"]
    assert third[1]["role"] == "tool" and third[1]["content"].startswith("update_needs: recorded")
    assert "Profile: kinds" in third[1]["content"]  # a new category gives its names
    assert all(answered(h) for h in llm.histories)
    # The first message stays the same in the whole turn, so the provider can cache it
    assert len({request.split("\n\nConversation:")[0] for _, request in llm.requests}) == 1


def test_the_problems_of_a_final_call_come_back_as_its_result():
    shop = Shop()
    llm = FakeLLM([[NEEDS], [PICKS], [SearchShop(words="kylskåp"), ReadReviews(ids=[1, 2])], [PICKS]])
    run(build_agent_graph(shop.deps(llm), InMemorySaver()), "A fridge at most 180 cm")
    history = llm.histories[2]
    final_id = history[-2]["tool_calls"][0]["id"]
    assert history[-1] == {"role": "tool", "tool_call_id": final_id, "content": history[-1]["content"]}
    assert "no shop search in this conversation yet" in history[-1]["content"]
    assert all(answered(h) for h in llm.histories)


def test_a_final_call_next_to_work_calls_waits_and_gets_a_result():
    shop = Shop()
    shop.reviewed = {1, 2}
    llm = FakeLLM([[NEEDS, SearchShop(words="kylskåp"), PICKS], [PICKS]])
    state = run(build_agent_graph(shop.deps(llm), InMemorySaver()), "A fridge at most 180 cm")
    assert "not run: other tools ran in the same step" in llm.requests[1][1]
    assert all(answered(h) for h in llm.histories) and [p["listing_id"] for p in state["picks"]] == [2, 1]


def test_a_page_of_another_shop_meets_the_shop_floor_and_can_be_a_pick():
    from scout.agent.loop import ReadPage
    shop = Shop()
    shop.reviewed = {4}
    read = []

    def read_page(url, profile):
        read.append((url, profile.category))
        return "id 4: Fridge 4 (elgiganten.se) | price=400 SEK", 1, 4
    deps = shop.deps(llm := FakeLLM([[NEEDS, ReadPage(url="https://www.elgiganten.se/product/4")],
                                     [ShowPicks(picks=[Pick(id=4, text="From Elgiganten.")])]]))
    deps.read_page = read_page
    state = run(build_agent_graph(deps, InMemorySaver()), "A fridge at most 180 cm")
    assert read == [("https://www.elgiganten.se/product/4", "fridge")]
    assert state["pages_read"] == [{"id": 4, "url": "https://www.elgiganten.se/product/4"}]
    assert [p["listing_id"] for p in state["picks"]] == [4] and shop.checked == [[4]]
    assert "read_page: id 4: Fridge 4 (elgiganten.se)" in llm.requests[1][1]


def test_a_page_that_was_not_read_does_not_count_for_the_floor():
    from scout.agent.loop import ReadPage
    shop = Shop()
    deps = shop.deps(llm := FakeLLM([[ReadPage(url="https://shop.se/x")], [NEEDS, ReadPage(url="https://shop.se/x")],
                                     "Ok."]))
    deps.read_page = lambda url, profile: (f"{url}: not read, the site shows a captcha.", 1, None)
    state = run(build_agent_graph(deps, InMemorySaver()), "A fridge at most 180 cm")
    assert "read_page: record the category with update_needs first." in llm.requests[1][1]
    assert not state.get("pages_read")


def test_a_page_of_another_shop_does_not_meet_the_shop_floor_for_amazon_picks():
    from scout.agent.loop import ReadPage
    shop = Shop()
    shop.reviewed = {1, 4}
    deps = shop.deps(llm := FakeLLM([[NEEDS, ReadPage(url="https://www.elgiganten.se/product/4")],
                                     [ShowPicks(picks=[Pick(id=4, text="A."), Pick(id=1, text="B.")])],
                                     [ShowPicks(picks=[Pick(id=4, text="A.")])]]))
    deps.read_page = lambda url, profile: ("id 4: Fridge 4 (elgiganten.se)", 1, 4)
    state = run(build_agent_graph(deps, InMemorySaver()), "A fridge at most 180 cm")
    assert "no shop search in this conversation yet" in llm.requests[2][1]
    assert [p["listing_id"] for p in state["picks"]] == [4]


def test_the_step_of_a_tool_call_for_the_web_page():
    from scout.agent.loop import ReadPage, SearchWeb, step_detail, step_title
    assert step_title(SearchWeb(question="Bomann KG 7358 test")) == ("web", "Search the web for 'Bomann KG 7358 test'")
    assert step_title(ReadPage(url="https://www.elgiganten.se/product/x")) == ("web", "Read a shop page on www.elgiganten.se")
    assert step_title(ReadReviews(ids=[1])) == ("reviews", "Read the amazon.se reviews of 1 product")
    assert step_detail("search_web: not available: no key.\nmore") == ("fail", "not available: no key.")
    assert step_detail("find_products: 12 fit.\n1. ...") == ("done", "12 fit.")
    state, text = step_detail("read_product: " + "x" * 400)
    assert state == "done" and len(text) == 160 and text.endswith("…")


def test_search_text_gives_the_words_of_each_language_to_one_search():
    from scout.agent.loop import SearchText, Toolbox
    seen = []
    deps = Shop().deps(FakeLLM([]))
    deps.search_text = lambda query, scope, k: seen.append(query) or []
    box = Toolbox(deps, {"needs": {"category": "fridge"}}, "t1")
    box.search_text(SearchText(question="tyst kompressor", more=["quiet compressor", "压缩机 安静"]))
    box.search_text(SearchText(question="tyst"))
    assert seen == [["tyst kompressor", "quiet compressor", "压缩机 安静"], "tyst"]


def test_think_runs_nothing_counts_as_a_call_and_shows_no_note():
    from scout.agent.loop import Think
    shop = Shop()
    steps = []
    deps = shop.deps(llm := FakeLLM([[Think(note="Two of five fit: ids 1 and 2")], "Ok."]))
    deps.step = steps.append
    graph = build_agent_graph(deps, InMemorySaver())
    run(graph, "Which fit?")
    assert "noted." in llm.requests[1][1]
    done = [s for s in steps if s.get("title") == "Think it over" and s["state"] == "done"]
    assert done[0]["used"]["calls"] == 1 and done[0]["kind"] == "note"
    assert not any("Two of five" in str(s) for s in steps)


def test_similar_products_passes_the_removed_products_and_gives_the_reason():
    from scout.agent.loop import SimilarProducts, Toolbox, step_detail, step_title
    from scout.text.retrieve import Hit, NoText, Similar
    seen = []
    shop = Shop()
    shop.removed.add(shop.cards[3].product_id)

    def similar(like, wishes, scope, k, exclude):
        seen.append((like, wishes, scope.category, k, exclude))
        if like == [4]:
            raise NoText("no indexed text for id 4")
        return [Similar(2, 1.3, Hit(12, 2, "review", "review 1", "Product: Fridge 2" + chr(10) + "Very quiet at night"))]
    deps = shop.deps(FakeLLM([]))
    deps.similar_products = similar
    box = Toolbox(deps, {"needs": {"category": "fridge"}}, "t1")
    text = box.similar_products(SimilarProducts(like=1, wish="tyst", more=["quiet"], limit=1))
    assert seen == [([1], ["tyst", "quiet"], "fridge", 1, {shop.cards[3].product_id})]
    assert "1 stored products nearest to id 1 and 'tyst'" in text and "id 2:" in text
    assert "score 1.300; nearest part (review 1): Very quiet at night" in text
    assert box.similar_products(SimilarProducts(like=4)).startswith("id 4 has no stored text to compare with")
    assert box.similar_products(SimilarProducts(like=9)) == "there is no product with id 9."
    assert box.similar_products(SimilarProducts()) == "give a product id, a wish or both."
    assert step_title(SimilarProducts(like=2, wish="quieter")) == ("db", "Find products near product 2 and 'quieter'")
    # A refusal of a tool shows as a failed step on the web page
    assert step_detail("similar_products: give a product id, a wish or both.")[0] == "fail"
    assert step_detail("update_needs: not recorded: no such attribute")[0] == "fail"


def test_a_tool_that_raises_does_not_leave_its_step_running():
    import pytest

    from scout.agent.loop import Think
    from scout.trace import NoTracer

    class Boom:
        def span(self, name):
            if name != "agent":
                raise RuntimeError("tracer down")
            return NoTracer().span(name)
    steps = []
    deps = Shop().deps(FakeLLM([[Think(note="x")], "Ok."]))
    deps.step, deps.tracer = steps.append, Boom()
    with pytest.raises(RuntimeError):
        run(build_agent_graph(deps, InMemorySaver()), "Hi")
    think = [s for s in steps if s.get("title") == "Think it over"]
    assert [s["state"] for s in think] == ["run", "fail"] and think[-1]["detail"] == "RuntimeError"


def test_find_products_orders_by_the_preferences_and_shows_the_verdicts():
    from scout.agent.loop import Toolbox
    from scout.llm.client import LLMUnavailable
    from scout.picks.wishes import Verdict
    from scout.text.retrieve import Hit
    seen = []
    shop = Shop()

    def judge(cards, wishes):
        seen.append(([c.listing_id for c in cards], wishes))
        return {1: {"quiet": Verdict("meets", Hit(11, 1, "review", "review 1", "Product: F\nSilent", {}, 0.9),
                                     "Silent")},
                4: {"quiet": Verdict("against", Hit(14, 4, "review", "review 2", "Nice. Very loud", {}, 0.8),
                                     "Very loud")}}
    deps = shop.deps(FakeLLM([]))
    deps.judge_wishes = judge
    box = Toolbox(deps, {"needs": {"category": "fridge", "preferences": ["quiet", " ", "quiet"]}}, "t1")
    lines = box.find_products(FindProducts(limit=4)).splitlines()
    assert seen == [([1, 2, 3, 4], ["quiet"])] and "ordered by the rating and the preferences" in lines[0]
    assert lines[1].startswith("id 1:") and lines[2] == '  meets: quiet (review 1: "Silent")'
    assert lines[3].startswith("id 4:") and lines[4] == '  against: quiet (review 2: "Very loud")'
    # 2 and 3 were not judged, so they come after the judged ones
    assert lines[5].startswith("id 2:") and lines[6].startswith("id 3:")
    assert box.used["shown"] == [1, 2, 3, 4]  # eval-hidden counts what the model saw

    def down(cards, wishes):
        raise LLMUnavailable("busy")
    deps.judge_wishes = down
    text = box.find_products(FindProducts())
    assert "ordered by the rating. The preferences could not be judged now (busy)." in text
    plain = Toolbox(shop.deps(FakeLLM([])), {"needs": {"category": "fridge"}}, "t1").find_products(FindProducts())
    assert "ordered by the rating." in plain and "meets" not in plain

def test_a_new_category_gets_a_draft_profile():
    from tests.test_clarify import HEADPHONES
    shop, saved = Shop(), {}
    llm = FakeLLM([[UpdateNeeds(category="headphones", kind="over_ear")], HEADPHONES, "Which budget?"])
    deps = shop.deps(llm)
    deps.profile_names = lambda: ["fridge", *saved]
    deps.load_profile = lambda name: saved.get(name) or FRIDGE
    deps.save_draft = lambda profile: saved.update({profile.category: profile})
    state = run(build_agent_graph(deps, InMemorySaver()), "Jag vill ha hörlurar")
    assert saved["headphones"].draft and state["needs"]["category"] == "headphones"
    report = llm.requests[2][1]
    assert "New category headphones: Python saved a draft profile" in report and "recorded:" in report
    assert "Profile: kinds over_ear" in report


def test_without_save_draft_a_new_category_is_refused():
    llm = FakeLLM([[UpdateNeeds(category="headphones")], "Sorry."])
    run(build_agent_graph(Shop().deps(llm), InMemorySaver()), "Jag vill ha hörlurar")
    assert "'headphones' is not a category. Use one of: fridge." in llm.requests[1][1]
