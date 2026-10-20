import threading

from langgraph.checkpoint.memory import InMemorySaver

from scout.agent.loop import Pick, ReadReviews, ResearchItem, ResearchProducts, ShowPicks, build_agent_graph
from scout.websearch import (Brief, BriefPoint, PageJudgement, ResearchReport, WebSearchUnavailable, quote_found,
                             research)
from tests.fakes import FakeLLM
from tests.test_loop import NEEDS
from tests.test_websearch import FORUM, JUDGED, PRODUCTS, TEST, TEST_PAGE, Store, WebShop, rerank


class Client:
    def __init__(self, results, texts=None, down=None):
        self.results, self.texts, self.down, self.queries = results, texts or {}, down, []
        self.lock = threading.Lock()

    def search(self, query, max_results=20):
        if self.down:
            raise WebSearchUnavailable(self.down)
        with self.lock:
            self.queries.append(query)
        return list(self.results)

    def extract(self, urls):
        return {u: self.texts[u] for u in urls if u in self.texts}


class Worker:
    """Judges the pages by their URL and sums up each product with fixed points."""

    def __init__(self, briefs):
        self.briefs, self.prompts = briefs, []

    def structured(self, role, system, user, schema, images=()):
        assert role == "worker"
        if schema is PageJudgement:
            return next((r for url, r in JUDGED.items() if f"URL: {url}\n" in user),
                        PageJudgement(kind="other", relevant=False))
        assert schema is Brief
        self.prompts.append(user)
        return next(b for title, b in self.briefs.items() if f"Product: {title}" in user)


def test_quote_found_ignores_spaces_case_and_quote_marks():
    text = "Vi mätte ljudnivån till 39 dB i vårt labb.\nFrysen är liten för en familj."
    assert quote_found("vi mätte  ljudnivån till 39 dB", text)
    assert quote_found("“Frysen är liten”", text)
    assert quote_found("Vi mätte ljudnivån ... en familj", text)
    assert not quote_found("Vi mätte ljudnivån till 35 dB", text)
    assert not quote_found("dB", text)  # too short to show anything


def test_each_product_gets_points_with_quotes_that_are_in_the_page():
    briefs = {
        PRODUCTS[0].title: Brief(points=[
            BriefPoint(text="Measured at 39 dB in a lab test.", page=1, quote="mätte ljudnivån till 39 dB"),
            BriefPoint(text="Very quiet, 30 dB.", page=1, quote="ljudnivån till 30 dB")],  # not in the page
            missing="Nothing about the energy use."),
        PRODUCTS[1].title: Brief(points=[BriefPoint(text="Louder than the Gorenje.", page=1,
                                                    quote="är billigare men låter mer")]),
    }
    store, worker = Store(), Worker(briefs)
    client = Client([TEST, FORUM], texts={TEST.url: TEST_PAGE})
    report = research([(PRODUCTS[0], "Gorenje RK4182PS4 test"), (PRODUCTS[1], "Bomann KG 320 test")], client, worker,
                      store.web_store(), rerank, max_pages=20)
    assert sorted(client.queries) == ["Bomann KG 320 test", "Gorenje RK4182PS4 test"]
    assert report.searches == 2 and report.pages == 4 and not report.unavailable
    assert sorted(q for q, _, _ in store.searches) == sorted(client.queries)
    text = report.text
    assert '- Measured at 39 dB in a lab test. "mätte ljudnivån till 39 dB" (testsite.se, test' in text
    assert "https://www.testsite.se/kyl" in text and "30 dB" not in text
    assert "(1 point(s) left out: the quote was not in the page.)" in text
    assert "Not in the pages: Nothing about the energy use." in text
    assert "[id 3] Bomann KG 320" in text and "låter mer" in text
    assert text.index("[id 2]") < text.index("[id 3]")  # the order of the products


def test_no_researcher_can_search():
    report = research([(PRODUCTS[0], "q")], Client([], down="no credits"), Worker({}), Store().web_store(), rerank,
                      max_pages=20)
    assert report.unavailable == "no credits" and "not researched: no credits" in report.text


class ResearchShop(WebShop):
    def __init__(self):
        super().__init__()
        self.research_calls = []

    def deps(self, llm):
        deps = super().deps(llm)
        deps.research_web = self.research
        return deps

    def research(self, items, max_pages, want=""):
        self.research_calls.append((items, max_pages, want))
        self.web |= {i for i, _ in items}
        return ResearchReport(f"Web research of {len(items)} products.", 6 * len(items), 0, len(items))


def test_research_products_meets_the_web_floor_and_counts_the_searches():
    shop = ResearchShop()
    steps = []
    research_call = ResearchProducts(products=[ResearchItem(id=2, words="RK4182 test"),
                                               ResearchItem(id=1, words="Fridge 1 test"),
                                               ResearchItem(id=9, words="x")], want="is it quiet")
    picks = ShowPicks(picks=[Pick(id=2, text="Quiet."), Pick(id=1, text="Fits.")])
    llm = FakeLLM([[NEEDS, ReadReviews(ids=[2, 1]), research_call], [picks]])
    deps = shop.deps(llm)
    deps.step = steps.append
    state = build_agent_graph(deps, InMemorySaver()).invoke(
        {"messages": [{"role": "user", "content": "A fridge, at most 180 cm"}], "searches": {"x": {"page": 1}}},
        {"configurable": {"thread_id": "t"}})
    assert shop.research_calls == [([(2, "RK4182 test"), (1, "Fridge 1 test")], 20, "is it quiet")]
    assert [p["listing_id"] for p in state["picks"]] == [2, 1]
    assert state["used"]["web_searches"] == 2 and state["used"]["web_pages"] == 12
    assert state["web_searches"] == ["RK4182 test", "Fridge 1 test"]
    assert "There is no product with id 9." in llm.requests[1][1]
    assert any(s["title"] == "Research 3 products on the web" for s in steps)
    assert any(x.startswith("Researching 2 products on the web") for x in shop.said), shop.said
