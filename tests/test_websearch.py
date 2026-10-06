import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver

from scout.agent.loop import Claim, Pick, ReadReviews, SearchWeb, ShowPicks, UpdateNeeds, build_agent_graph
from scout.llm.client import LLMUnavailable
from scout.websearch import (MAX_TO_CHAT, PAGE_CHARS, PageJudgement, ProductRef, TavilyClient, WebResult,
                             WebSearchUnavailable, WebStore, search_web, url_ref)
from tests.fakes import FakeLLM
from tests.test_loop import NEEDS, Shop, run

TEST_PAGE = ("Gorenje RK4182PS4 i test. Vi mätte ljudnivån till 39 dB i vårt labb. Frysen är liten för en familj. "
             "Bomann KG 320 är billigare men låter mer.")
TEST = WebResult("https://www.testsite.se/kyl", "Test av kylskåp 2026", "Gorenje i test", TEST_PAGE)
FORUM = WebResult("https://forum.example.com/t/1", "Vilken kyl?", "Tråd", "Min Gorenje RK4182PS4 har gått i 3 år.")
SHOP = WebResult("https://www.elgiganten.se/product/1", "Gorenje RK4182PS4", "Köp", "Pris 5990 kr. Lägg i varukorg.")
OTHERS = [WebResult(f"https://site{n}.se/", f"Page {n}", "", f"Text of page {n}.") for n in range(1, 5)]
PRODUCTS = [ProductRef(2, "Gorenje RK4182PS4 kyl/frys", "RK4182PS4"), ProductRef(3, "Bomann KG 320", "KG 320")]
NOT = PageJudgement(kind="other", relevant=False, reason="no test")


class Worker:
    """Answers each page by its URL, so the order of the parallel calls does not matter."""

    def __init__(self, by_url, default=NOT):
        self.by_url, self.default, self.prompts = by_url, default, []

    def structured(self, role, system, user, schema, images=()):
        assert role == "worker" and schema is PageJudgement
        self.prompts.append(user)
        reply = next((r for url, r in self.by_url.items() if f"URL: {url}\n" in user), self.default)
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

    def search(self, query, max_results=20):
        self.queries.append(query)
        return list(self.results)


JUDGED = {
    TEST.url: PageJudgement(kind="test", relevant=True, about=[2, 3, 9], reason="a lab test"),
    FORUM.url: PageJudgement(kind="forum", relevant=True, about=[2]),
    SHOP.url: PageJudgement(kind="shop", relevant=False, about=[2], reason="only a price"),
}


def test_the_worker_judges_each_page_and_the_chat_model_gets_the_text_of_at_least_5():
    store, client, worker = Store(), Client([SHOP, TEST, *OTHERS, FORUM]), Worker(JUDGED)
    report = search_web("Gorenje RK4182PS4 test", PRODUCTS, client, worker, store.web_store())
    assert (report.pages, report.credits, report.to_chat) == (7, 1, 5) and len(worker.prompts) == 7
    assert "Search question: Gorenje RK4182PS4 test" in worker.prompts[0]
    # the relevant pages first, then the next pages with text up to 5; the full text, not a summary
    order = [line for line in report.text.splitlines() if line.startswith("[web page")]
    assert [o.split("] ")[1].split(",")[0] for o in order] == ["testsite.se", "forum.example.com", "elgiganten.se",
                                                               "site1.se", "site2.se"]
    assert TEST_PAGE in report.text and "Text of page 2." in report.text and "Text of page 3." not in report.text
    assert "about id 2, 3:" in report.text and "id 9" not in report.text  # an id that was not asked for is dropped
    assert "(the worker found it not relevant)" in order[2]
    assert "- site3.se, other: Page 3 (https://site3.se/): not relevant, no test" in report.text
    # the test and forum pages are stored as chunks of the products that they are about; a shop page is not
    assert set(store.chunks) == {(2, TEST.url), (3, TEST.url), (2, FORUM.url)}
    chunk = store.chunks[(2, TEST.url)][0]
    assert chunk.kind == "test" and chunk.ref == url_ref(TEST.url) + " 1" and chunk.text.endswith(TEST_PAGE)
    assert chunk.meta == {"url": TEST.url, "title": TEST.title, "site": "testsite.se", "type": "test"}
    assert store.searches == [("Gorenje RK4182PS4 test", [2, 3])]


def test_at_most_8_pages_go_to_the_chat_model():
    pages = [WebResult(f"https://t{n}.se/", f"T{n}", "", f"Test {n}.") for n in range(12)]
    report = search_web("q", [], Client(pages), Worker({}, PageJudgement(kind="test", relevant=True)),
                        Store().web_store())
    assert report.to_chat == MAX_TO_CHAT == 8
    assert "- t8.se, test: T8 (https://t8.se/): relevant" in report.text


def test_a_long_page_is_cut_for_the_chat_model():
    long = WebResult("https://long.se/", "Long", "", "x" * (PAGE_CHARS + 10))
    report = search_web("q", [], Client([long]), Worker({}), Store().web_store())
    assert "x" * PAGE_CHARS + " [cut here]" in report.text


def test_stored_results_of_the_question_cost_no_credit():
    store, client = Store(stored=[FORUM]), Client([TEST])
    report = search_web("gorenje rk4182ps4 test", PRODUCTS[:1], client, Worker(JUDGED), store.web_store())
    assert client.queries == [] and report.credits == 0 and "stored results" in report.text
    assert store.searches  # the floor counts the search again


def test_without_products_nothing_is_stored():
    store = Store()
    report = search_web("bästa kylskåp test 2026", [], Client([TEST, FORUM]), Worker(JUDGED), store.web_store())
    assert report.pages == 2 and store.chunks == {} and TEST_PAGE in report.text


def test_the_page_limit_and_a_worker_that_stops():
    judged = {**JUDGED, FORUM.url: LLMUnavailable("quota")}
    report = search_web("q", PRODUCTS, Client([TEST, FORUM, SHOP]), Worker(judged), Store().web_store(), max_pages=2)
    assert report.pages == 2
    assert "1 more pages were found and not judged" in report.text
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
    assert shop.web_calls == [("RK4182 test", [2], 60)]
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
    assert "the web pages about this product do not show 41 dB" in llm.requests[2][1]
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
def test_web_searches_count_for_the_product_and_web_text_loses_its_header(db):
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
    finding = Finding(TEST, "test", True, [1])
    for _ in range(2):  # a second read of the page replaces its chunks
        save_web_chunks(db, 1, url_ref(TEST.url), web_chunks("Fridge 2026 model", finding), now)
    assert db.execute("SELECT count(*) AS n FROM chunks WHERE source = 'web'").fetchone()["n"] == 1
    assert web_texts(db, [2]) == {2: TEST_PAGE}  # without the two lines that name the product and the page
