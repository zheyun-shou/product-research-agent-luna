import copy

from langgraph.checkpoint.memory import InMemorySaver

from scout.agent.clarify import CategoryChoice, ClarifyReply
from scout.agent.followup import AnswerClaim, NextStep, RemovePicks, Reply, RestorePicks
from scout.agent.graph import Deps, build_graph
from scout.crawl import CrawlReport
from scout.llm.client import QuotaExhausted
from scout.needs import Needs
from scout.picks.pipeline import PicksResult
from scout.profiles import load_profile
from tests.fakes import FakeLLM
from tests.test_picks_rank import card

FRIDGE = load_profile("fridge")
TERMS = "kyl och frys"


class World:
    """A fake shop and database behind the graph. It shows k picks out of the stored cards."""

    def __init__(self, stored=20, n=4, k=2, doubts=None):
        self.stored, self.k, self.doubts = stored, k, doubts or {}
        self.crawls, self.removed, self.said, self.picked = [], {}, [], []
        heights = [143.0, 170.0, 150.0, 160.0]
        self.cards = {i: card(i, 4.5, 100, height=heights[i - 1]) for i in range(1, n + 1)}

    def deps(self, llm):
        return Deps(
            llm=llm,
            profile_names=lambda: ["fridge"],
            load_profile=lambda name: FRIDGE,
            count=lambda profile, needs, excluded: self.stored,
            crawl=self.crawl,
            picks=self.picks,
            cards=lambda ids: [copy.deepcopy(self.cards[i]) for i in ids],  # fresh from the database
            eliminate=lambda thread, ids, reason: self.removed.setdefault(thread, set()).update(ids),
            eliminated=lambda thread: self.removed.get(thread, set()),
            restore=lambda thread, ids: self.removed.setdefault(thread, set()).difference_update(ids),
            say=self.said.append,
        )

    def crawl(self, profile, needs, page):
        self.crawls.append((needs.search_terms(profile), page))
        return CrawlReport(candidates=48, saved=20)

    def picks(self, profile, needs, excluded, kept, first, language="English"):
        self.picked.append((set(excluded), [c.listing_id for c in kept], [c.listing_id for c in first]))
        kept = kept[:max(0, self.k - len(first))]
        taken = set(excluded) | {c.product_id for c in [*kept, *first]}
        others = [copy.deepcopy(c) for c in self.cards.values() if c.product_id not in taken]
        new = [*first, *others][:self.k - len(kept)]
        notes = {}
        for c in new:
            if c.listing_id in self.doubts:
                c.attributes["height"] = {**c.attributes["height"], "doubtful": True}
                notes[c.listing_id] = self.doubts[c.listing_id]
        return PicksResult([*kept, *new], None, [], list(notes.values()), 0, needs.text(), notes)


def run(graph, thread, text):
    return graph.invoke({"messages": [{"role": "user", "content": text}]}, {"configurable": {"thread_id": thread}})


def ready(**fields):
    return ClarifyReply(needs=Needs(kind="fridge_freezer", **fields), ready=True)


def shown(state):
    return [p["listing_id"] for p in state["picks"]]


def test_clarify_asks_first_and_does_not_search():
    world = World()
    llm = FakeLLM([CategoryChoice(category="fridge"), ClarifyReply(needs=Needs(), ready=False, question="Which kind?")])
    state = run(build_graph(world.deps(llm), InMemorySaver()), "t1", "I want a fridge")
    assert state["messages"][-1]["content"] == "Which kind?"
    assert state["phase"] == "clarify" and world.crawls == []


def test_a_new_thread_reads_page_1_once_then_uses_the_stored_products():
    world = World(stored=20)
    llm = FakeLLM([CategoryChoice(category="fridge"), ready(budget=9000), [NextStep(step="change_needs")],
                   ready(budget=8000)])
    graph = build_graph(world.deps(llm), InMemorySaver())
    state = run(graph, "t1", "A fridge with a freezer, budget 9000")
    assert world.crawls == [(TERMS, 1)] and state["searches"] == {TERMS: {"page": 1, "budget": 9000}}
    assert state["phase"] == "picks" and shown(state) == [1, 2]
    assert state["messages"][-1]["content"].startswith("Conditions: budget 9000 SEK")
    state = run(graph, "t1", "Make the budget 8000")
    assert world.crawls == [(TERMS, 1)]
    assert "enough stored products match" in state["messages"][-2]["content"]


def test_a_higher_budget_reads_page_1_again():
    world = World(stored=20)
    llm = FakeLLM([CategoryChoice(category="fridge"), ready(budget=9000), [NextStep(step="change_needs")],
                   ready(budget=12000), [NextStep(step="change_needs")], ready(budget=10000)])
    graph = build_graph(world.deps(llm), InMemorySaver())
    run(graph, "t1", "A fridge with a freezer, budget 9000")
    state = run(graph, "t1", "Make the budget 12000")
    assert world.crawls == [(TERMS, 1), (TERMS, 1)] and state["searches"][TERMS] == {"page": 1, "budget": 12000}
    assert "the budget is higher than in the earlier search" in world.said[-1]
    run(graph, "t1", "Make the budget 10000")
    assert len(world.crawls) == 2


def test_few_stored_products_start_a_search_with_the_kind_terms():
    world = World(stored=3)
    llm = FakeLLM([CategoryChoice(category="fridge"), ready()])
    state = run(build_graph(world.deps(llm), InMemorySaver()), "t1", "A fridge with a freezer")
    assert world.crawls == [(TERMS, 1)] and state["searches"] == {TERMS: {"page": 1, "budget": None}}
    assert f"Searched amazon.se for '{TERMS}'" in state["messages"][-2]["content"]
    assert world.said and TERMS in world.said[0]


def test_follow_ups_in_one_thread():
    world = World()
    llm = FakeLLM([CategoryChoice(category="fridge"), ready(),
                   [RemovePicks(picks=[1], words="the first one", reason="too small")],
                   [Reply(text="Pick 1 is 170 cm high.", claims=[AnswerClaim(pick=1, field="height", value="170")])],
                   [NextStep(step="show_more")]])
    graph = build_graph(world.deps(llm), InMemorySaver())
    run(graph, "t1", "A fridge with a freezer")
    state = run(graph, "t1", "Remove the first one, it is too small")
    assert world.removed["t1"] == {1} and shown(state) == [2, 3]
    assert world.picked[-1] == ({1}, [2], [])  # pick 2 stays without a new check
    assert state["removed"] == [{"product_id": 1, "listing_id": 1, "title": "Fridge 1"}]
    state = run(graph, "t1", "How tall is it?")
    assert state["messages"][-1]["content"] == "Pick 1 is 170 cm high."
    state = run(graph, "t1", "Show me more")
    assert world.crawls == [(TERMS, 1), (TERMS, 2)] and state["phase"] == "picks"
    assert world.picked[-1] == ({1, 2, 3}, [], []) and shown(state) == [4]


def test_more_keeps_the_picks_when_no_other_product_matches():
    world = World(n=2)
    llm = FakeLLM([CategoryChoice(category="fridge"), ready(), [NextStep(step="show_more")]])
    graph = build_graph(world.deps(llm), InMemorySaver())
    run(graph, "t1", "A fridge with a freezer")
    state = run(graph, "t1", "Show me others")
    assert "No other stored product" in state["messages"][-1]["content"] and shown(state) == [1, 2]


def test_recheck_checks_the_shown_picks_first():
    world = World()
    llm = FakeLLM([CategoryChoice(category="fridge"), ready(), [NextStep(step="recheck")]])
    graph = build_graph(world.deps(llm), InMemorySaver())
    run(graph, "t1", "A fridge with a freezer")
    run(graph, "t1", "Are the prices still right?")
    assert world.picked[-1] == (set(), [], [1, 2])


def test_a_kept_pick_keeps_its_size_doubt():
    world = World(doubts={2: "Fridge 2: height may be wrong."})
    llm = FakeLLM([CategoryChoice(category="fridge"), ready(),
                   [RemovePicks(picks=[1], words="Remove 1")], [Reply(text="See the picks.")]])
    graph = build_graph(world.deps(llm), InMemorySaver())
    run(graph, "t1", "A fridge with a freezer")
    state = run(graph, "t1", "Remove 1")
    assert state["picks"][0]["doubtful"] == ["height"] and state["picks"][0]["note"] == "Fridge 2: height may be wrong."
    assert "Fridge 2: height may be wrong." in state["messages"][-1]["content"]
    run(graph, "t1", "How tall is pick 1?")
    assert "height=170.0 (doubtful)" in llm.requests[-1][1]


def test_an_unclear_removal_asks_which_pick_and_removes_nothing():
    world = World()
    llm = FakeLLM([CategoryChoice(category="fridge"), ready(),
                   "Which pick do you mean, 1 or 2?"])
    graph = build_graph(world.deps(llm), InMemorySaver())
    run(graph, "t1", "A fridge with a freezer")
    state = run(graph, "t1", "Remove that one")
    assert state["messages"][-1]["content"] == "Which pick do you mean, 1 or 2?"
    assert world.removed == {} and shown(state) == [1, 2] and len(world.picked) == 1


def test_a_removed_product_can_come_back():
    world = World()
    llm = FakeLLM([CategoryChoice(category="fridge"), ready(),
                   [RemovePicks(picks=[1], words="pick 1")],
                   [RestorePicks(product_ids=[1]), Reply(text="Pick 1 is back, as you asked.")]])
    graph = build_graph(world.deps(llm), InMemorySaver())
    run(graph, "t1", "A fridge with a freezer")
    run(graph, "t1", "Remove pick 1")
    state = run(graph, "t1", "No, I want it back")
    assert world.removed["t1"] == set() and state["removed"] == []
    assert world.picked[-1] == (set(), [2, 3], [1]) and shown(state) == [2, 1]
    texts = [m["content"] for m in state["messages"][-3:]]
    assert texts[0] == "Brought back: Fridge 1." and texts[1] == "Pick 1 is back, as you asked."


def test_a_reply_that_fails_three_rounds_changes_nothing():
    world = World()
    bad = [Reply(text="Removed pick 1, it is 80 cm high.")]
    llm = FakeLLM([CategoryChoice(category="fridge"), ready(), bad, bad, bad])
    graph = build_graph(world.deps(llm), InMemorySaver())
    run(graph, "t1", "A fridge with a freezer")
    state = run(graph, "t1", "Remove the small one")
    assert "so I changed nothing" in state["messages"][-1]["content"] and world.removed == {}
    assert "the reply says 'Removed'" in llm.requests[-1][1]


def test_an_unavailable_model_gives_a_message_and_the_turn_ends():
    world = World()
    llm = FakeLLM([QuotaExhausted("The free daily quota is used up.")])
    state = run(build_graph(world.deps(llm), InMemorySaver()), "t1", "I want a fridge")
    assert "quota is used up" in state["messages"][-1]["content"]
    assert state.get("phase") is None and world.crawls == []


def test_a_browser_problem_ends_the_turn_with_a_message():
    world = World(stored=3)

    def broken(profile, needs, page):
        raise RuntimeError("Target page, context or browser has been closed")

    world.crawl = broken
    llm = FakeLLM([CategoryChoice(category="fridge"), ready()])
    state = run(build_graph(world.deps(llm), InMemorySaver()), "t1", "A fridge with a freezer")
    assert "Something went wrong in the step 'search' (RuntimeError" in state["messages"][-1]["content"]
    assert world.picked == [] and state["needs"]["kind"] == "fridge_freezer"


def test_a_product_without_a_category_gets_a_draft_profile():
    from tests.test_clarify import HEADPHONES
    world, saved = World(), []
    deps = world.deps(FakeLLM([CategoryChoice(new_category="headphones"), HEADPHONES,
                               ClarifyReply(needs=Needs(), ready=False, question="Over-ear or in-ear?")]))
    deps.save_draft = saved.append
    deps.load_profile = lambda name: saved[0] if name == "headphones" else FRIDGE
    state = run(build_graph(deps, InMemorySaver()), "t1", "I need headphones")
    assert saved[0].draft and state["needs"]["category"] == "headphones"
    assert world.said[0].startswith("New category drafted: headphones. Kinds: Over-ear headphones.")
    assert [m["content"] for m in state["messages"][1:]] == ["Over-ear or in-ear?"]


def test_reviews_are_read_after_the_picks():
    world = World(stored=3)
    asked = []
    deps = world.deps(FakeLLM([CategoryChoice(category="fridge"), ready()]))
    deps.read_reviews = lambda picks: asked.append([p["listing_id"] for p in picks]) or "Read the shop reviews."
    state = run(build_graph(deps, InMemorySaver()), "t1", "A fridge with a freezer")
    assert asked == [[1, 2]]
    assert state["messages"][-1]["content"] == "Read the shop reviews."
    assert [p["listing_id"] for p in state["picks"]] == [1, 2]


def test_no_review_message_when_nothing_was_read():
    world = World(stored=3)
    deps = world.deps(FakeLLM([CategoryChoice(category="fridge"), ready()]))
    deps.read_reviews = lambda picks: ""
    state = run(build_graph(deps, InMemorySaver()), "t1", "A fridge with a freezer")
    assert state["messages"][-1]["content"] != ""
    assert len([m for m in state["messages"] if m["role"] == "assistant"]) == 2
