import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver

from scout.agent.loop import Claim, Pick, ReadReviews, SearchWeb, ShowPicks, UpdateNeeds, build_agent_graph
from scout.llm.client import LLMUnavailable
from scout.websearch import (PageRead, ProductQuotes, ProductRef, TavilyClient, WebResult, WebSearchUnavailable,
                             WebStore, search_web, url_ref)
from tests.fakes import FakeLLM
from tests.test_loop import NEEDS, Shop, run

TEST_PAGE = ("Gorenje RK4182PS4 i test. Vi mätte ljudnivån till 39 dB i vårt labb. Frysen är liten för en familj. "
             "Bomann KG 320 är billigare men låter mer.")
TEST = WebResult("https://www.testsite.se/kyl", "Test av kylskåp 2026", "Gorenje i test", TEST_PAGE)
FORUM = WebResult("https://forum.example.com/t/1", "Vilken kyl?", "Tråd", "Min Gorenje RK4182PS4 har gått i 3 år.")
SHOP = WebResult("https://www.elgiganten.se/product/1", "Gorenje RK4182PS4", "Köp", "Pris 5990 kr. Lägg i varukorg.")
PRODUCTS = [ProductRef(2, "Gorenje RK4182PS4 kyl/frys", "RK4182PS4"), ProductRef(3, "Bomann KG 320", "KG 320")]


class Worker:
    """Answers each page by its URL, so the order of the parallel calls does not matter."""

    def __init__(self, by_url):
        self.by_url, self.prompts = by_url, []

    def structured(self, role, system, user, schema, images=()):
        assert role == "worker" and schema is PageRead
        self.prompts.append(user)
        reply = next(r for url, r in self.by_url.items() if f"URL: {url}" in user)
        if isinstance(reply, Exception):
            raise reply
        return reply


class Store:
    def __init__(self, stored=None):
        self.results, self.searches, self.chunks = stored, [], {}

    def web_store(self):
        return WebStore(stored=lambda q: self.results, save_search=lambda q, ids, rs: self.searches.append((q, ids)),
                        save_chunks=lambda listing, url, chunks: self.chunks.__setitem__((listing, url), chunks))


class Client:
    def __init__(self, results):
        self.results, self.queries = results, []

    def search(self, query, max_results=5):
        self.queries.append(query)
        return list(self.results)


READS = {
    TEST.url: PageRead(kind="test", products=[
        ProductQuotes(id=2, quotes=["Vi mätte ljudnivån till 39 dB i vårt labb.", "Den bästa kylen i år."]),
        ProductQuotes(id=3, quotes=["Bomann KG 320 är billigare men låter mer."]),
        ProductQuotes(id=9, quotes=["Gorenje RK4182PS4 i test."])]),
    FORUM.url: PageRead(kind="forum", products=[ProductQuotes(id=2, quotes=["Min Gorenje RK4182PS4 har gått i 3 år."])]),
    SHOP.url: PageRead(kind="shop", products=[ProductQuotes(id=2, quotes=["Pris 5990 kr."])]),
}


def test_quotes_that_are_in_the_page_become_chunks_of_tests_and_forums():
    store, client = Store(), Client([TEST, FORUM, SHOP])
    report = search_web("Gorenje RK4182PS4 test", PRODUCTS, client, Worker(READS), store.web_store())
    assert (report.pages, report.credits) == (3, 1) and client.queries == ["Gorenje RK4182PS4 test"]
    test = store.chunks[(2, TEST.url)]
    assert [c.text.split("\n")[-1] for c in test] == ["Vi mätte ljudnivån till 39 dB i vårt labb."]
    assert test[0].kind == "test" and test[0].ref == url_ref(TEST.url) + " 1"
    assert test[0].meta == {"url": TEST.url, "title": TEST.title, "site": "testsite.se", "type": "test"}
    assert [c.kind for c in store.chunks[(2, FORUM.url)]] == ["forum"]
    assert len(store.chunks[(3, TEST.url)]) == 1
    assert (2, SHOP.url) not in store.chunks  # a shop page is not a test
    assert "1 quotes of the worker were not in the page" in report.text  # the invented quote
    assert "a shop page" in report.text and "id 9" not in report.text  # an id that was not asked for is dropped
    assert store.searches == [("Gorenje RK4182PS4 test", [2, 3])]


def test_stored_results_of_the_question_cost_no_credit():
    store, client = Store(stored=[FORUM]), Client([TEST])
    report = search_web("gorenje rk4182ps4 test", PRODUCTS[:1], client, Worker(READS), store.web_store())
    assert client.queries == [] and report.credits == 0 and "stored results" in report.text
    assert store.searches  # the floor counts the search again


def test_without_products_no_page_is_read():
    worker = Worker(READS)
    report = search_web("bästa kylskåp test 2026", [], Client([TEST, FORUM]), worker, Store().web_store())
    assert report.pages == 0 and worker.prompts == []
    assert TEST.url in report.text and "Gorenje i test" in report.text


def test_the_page_limit_and_a_worker_that_stops():
    reads = {**READS, FORUM.url: LLMUnavailable("quota")}
    report = search_web("q", PRODUCTS, Client([TEST, FORUM, SHOP]), Worker(reads), Store().web_store(), max_pages=2)
    assert report.pages == 2
    assert "1 more pages were found and not read" in report.text
    assert "worker model is not available" in report.text


def tavily(handler):
    return TavilyClient("key", http=httpx.Client(transport=httpx.MockTransport(handler)))


def test_the_tavily_request_and_its_results():
    sent = []

    def handler(request):
        sent.append(request)
        return httpx.Response(200, json={"results": [{"url": "https://a.se/x", "title": "T", "content": "c",
                                                      "raw_content": "full text", "score": 0.9}]})
    results = tavily(handler).search("kyl test")
    assert results == [WebResult("https://a.se/x", "T", "c", "full text")]
    assert sent[0].headers["Authorization"] == "Bearer key"
    body = json.loads(sent[0].content)
    assert body["query"] == "kyl test" and body["search_depth"] == "basic" and body["include_raw_content"] == "text"


@pytest.mark.parametrize("status", [401, 429, 432, 500])
def test_a_tavily_error_makes_web_search_unavailable(status):
    with pytest.raises(WebSearchUnavailable):
        tavily(lambda request: httpx.Response(status, json={})).search("q")


# ---- the loop ----

class WebShop(Shop):
    def __init__(self):
        super().__init__()
        self.web, self.web_calls, self.down = set(), [], None
        self.quotes = {2: "Vi mätte 39 dB."}

    def deps(self, llm):
        deps = super().deps(llm)
        deps.search_web, deps.web_searched = self.search_web, lambda ids: {i for i in ids if i in self.web}
        deps.web_texts = lambda ids: {i: self.quotes[i] for i in ids if i in self.quotes}
        return deps

    def search_web(self, question, ids, max_pages):
        if self.down:
            raise WebSearchUnavailable(self.down)
        self.web_calls.append((question, ids, max_pages))
        self.web |= set(ids)
        return f"Web search '{question}': read 3 pages.", 3


PICK = ShowPicks(picks=[Pick(id=2, text="Quiet, 39 dB in a test.", claims=[Claim(id=2, field="web", value="39 dB")])])


def test_the_picks_wait_for_a_web_search_of_each_pick():
    shop = WebShop()
    llm = FakeLLM([[NEEDS, ReadReviews(ids=[2])], [PICK], [SearchWeb(question="RK4182 test", ids=[2])], [PICK]])
    graph = build_agent_graph(shop.deps(llm), InMemorySaver())
    state = graph.invoke({"messages": [{"role": "user", "content": "A fridge, at most 180 cm"}], "searches": {"x": {"page": 1}}},
                         {"configurable": {"thread_id": "t"}})
    assert "id 2 had no web search for tests yet" in llm.requests[2][1]
    assert shop.web_calls == [("RK4182 test", [2], 20)]
    assert [p["listing_id"] for p in state["picks"]] == [2]
    assert state["web_searches"] == ["RK4182 test"] and state["used"]["web_pages"] == 3


def test_a_web_claim_needs_the_number_in_the_quotes():
    shop = WebShop()
    shop.web, shop.reviewed = {2}, {2}
    wrong = ShowPicks(picks=[Pick(id=2, text="41 dB in a test.", claims=[Claim(id=2, field="web", value="41 dB")])])
    llm = FakeLLM([[NEEDS], [wrong], [PICK]])
    graph = build_agent_graph(shop.deps(llm), InMemorySaver())
    state = graph.invoke({"messages": [{"role": "user", "content": "A fridge, at most 180 cm"}], "searches": {"x": {"page": 1}}},
                         {"configurable": {"thread_id": "t"}})
    assert "the web quotes about this product do not show 41 dB" in llm.requests[2][1]
    assert [p["listing_id"] for p in state["picks"]] == [2]


def test_no_floor_when_the_shopper_does_not_want_it_or_tavily_is_down():
    for first in ([UpdateNeeds(**{**NEEDS.model_dump(), "web_tests": False})],
                  [NEEDS, SearchWeb(question="q", ids=[2])]):
        shop = WebShop()
        shop.reviewed, shop.down = {2}, "no credits"
        picks = ShowPicks(picks=[Pick(id=2, text="Fits.")])
        llm = FakeLLM([first, [picks]])
        graph = build_agent_graph(shop.deps(llm), InMemorySaver())
        state = graph.invoke({"messages": [{"role": "user", "content": "A fridge, at most 180 cm"}], "searches": {"x": {"page": 1}}},
                             {"configurable": {"thread_id": "t"}})
        assert [p["listing_id"] for p in state["picks"]] == [2], first


def test_search_web_without_a_key():
    shop = Shop()  # no search_web: no floor either
    llm = FakeLLM([[SearchWeb(question="q")], "No web search."])
    run(build_agent_graph(shop.deps(llm), InMemorySaver()), "a fridge")
    assert "not available: no Tavily key is set." in llm.requests[1][1]


# ---- the database ----

@pytest.mark.db
def test_web_searches_count_for_the_product_and_quotes_lose_their_header(db):
    from scout.db.store import (save_web_chunks, save_web_search, stored_web_results, web_searched, web_texts)
    from scout.websearch import Finding, web_chunks
    db.execute("INSERT INTO products (category, name) VALUES ('fridge', 'P1'), ('fridge', 'P2')")
    db.execute("""INSERT INTO listings (product_id, source, source_id, url, fetched_at) VALUES
                  (1, 'amazon.se', 'A', 'u1', now()), (1, 'other', 'B', 'u2', now()), (2, 'amazon.se', 'C', 'u3', now())""")
    now = datetime.now(timezone.utc)
    save_web_search(db, " Gorenje test ", [2], [vars(TEST)])
    assert web_searched(db, [1, 2, 3], now - timedelta(days=7)) == {1, 2}  # listing 1 is the same product
    assert web_searched(db, [1], now + timedelta(minutes=1)) == set()
    assert stored_web_results(db, "gorenje TEST", now - timedelta(days=7))[0]["url"] == TEST.url
    assert stored_web_results(db, "other", now - timedelta(days=7)) is None
    finding = Finding(TEST, "test", {1: ["Vi mätte ljudnivån till 39 dB i vårt labb."]})
    for _ in range(2):  # a second read of the page replaces its chunks
        save_web_chunks(db, 1, url_ref(TEST.url), web_chunks("Fridge 2026 model", finding, 1), now)
    assert db.execute("SELECT count(*) AS n FROM chunks WHERE source = 'web'").fetchone()["n"] == 1
    assert web_texts(db, [2]) == {2: "Vi mätte ljudnivån till 39 dB i vårt labb."}
