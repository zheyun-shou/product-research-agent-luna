import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver

from scout.agent.loop import Claim, Pick, ReadReviews, SearchWeb, ShowPicks, UpdateNeeds, build_agent_graph
from scout.llm.client import LLMUnavailable
from scout.websearch import (MAX_CHAT_PARTS, PageJudgement, Part, ProductRef, TavilyClient, WebResult,
                             WebSearchUnavailable, WebStore, choose_parts, search_web, topic_question, url_ref)
from tests.fakes import FakeLLM
from tests.test_loop import NEEDS, Shop, run

TEST_PAGE = ("Gorenje RK4182PS4 i test.\n\nVi mätte ljudnivån till 39 dB i vårt labb.\n\nFrysen är liten för en "
             "familj.\n\nBomann KG 320 är billigare men låter mer.")
TEST = WebResult("https://www.testsite.se/kyl", "Test av kylskåp 2026", "Gorenje i test", "", "2025-03-08")
FORUM = WebResult("https://forum.example.com/t/1", "Vilken kyl?", "Tråd", "Min Gorenje RK4182PS4 är tyst.")
SHOP = WebResult("https://www.elgiganten.se/product/1", "Gorenje RK4182PS4", "Köp", "Pris 5990 kr.")
OTHER = WebResult("https://site.se/", "Recept", "Kakor", "Text om kakor.")
PRODUCTS = [ProductRef(2, "Gorenje RK4182PS4 kyl/frys", "RK4182PS4", "Gorenje"), ProductRef(3, "Bomann KG 320", "KG 320", "Bomann")]
NOT = PageJudgement(kind="other", relevant=False, reason="about cakes")
JUDGED = {
    TEST.url: PageJudgement(kind="test", relevant=True, about=[2, 3, 9]),
    FORUM.url: PageJudgement(kind="forum", relevant=True, about=[2]),
    SHOP.url: PageJudgement(kind="shop", relevant=False, about=[2], reason="only a price"),
}
FILLER = "Lorem ipsum " * 82  # about 245 tokens: no other paragraph fits into its part


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
        return WebStore(stored=lambda q: self.results,
                        save_search=lambda q, ids, rs: self.searches.append((q, ids, rs)),
                        save_chunks=lambda listing, url, chunks: self.chunks.__setitem__((listing, url), chunks))


class Client:
    def __init__(self, results, texts=None):
        self.results, self.texts, self.queries, self.extracted = results, texts or {}, [], []

    def search(self, query, max_results=20):
        self.queries.append(query)
        return list(self.results)

    def extract(self, urls):
        self.extracted.append(list(urls))
        return {u: self.texts[u] for u in urls if u in self.texts}


def rerank(question, texts):
    """0.9 for a part about the noise, 0.2 for the others."""
    return [0.9 if ("dB" in t or "tyst" in t) else 0.2 for t in texts]


def run_search(results, texts=None, products=PRODUCTS, store=None, **kw):
    store, client, worker = store or Store(), Client(results, texts), Worker(JUDGED)
    report = search_web("Gorenje RK4182PS4 ljudnivå", products, client, worker, store.web_store(), rerank, **kw)
    return report, store, client, worker


def test_the_worker_judges_the_short_parts_and_the_relevant_pages_are_extracted():
    report, store, client, worker = run_search([SHOP, TEST, OTHER, FORUM], {TEST.url: TEST_PAGE})
    assert len(worker.prompts) == 4 and "Short part: Gorenje i test" in worker.prompts[1]
    assert "Published: 2025-03-08" in worker.prompts[1] and "Vi mätte" not in "".join(worker.prompts)
    assert client.extracted == [[TEST.url]]  # the forum page came with its text, the others are not relevant
    assert (report.pages, report.credits) == (4, 2)  # the search and one extraction
    # only the relevant pages keep their text
    saved = {r.url: r.raw for r in store.searches[0][2]}
    assert saved[TEST.url] == TEST_PAGE and saved[SHOP.url] == "" and saved[OTHER.url] == ""
    assert set(store.chunks) == {(2, TEST.url), (3, TEST.url), (2, FORUM.url)}  # id 9 was not asked for
    chunk = store.chunks[(2, TEST.url)][0]
    assert chunk.kind == "test" and chunk.ref == url_ref(TEST.url) + " 1" and chunk.meta["published"] == "2025-03-08"


def test_extract_finds_an_address_in_another_form_and_its_error_keeps_the_paid_search():
    class OtherForm(Client):  # Tavily can give back the address in another form
        def extract(self, urls):
            return {"http://testsite.se/kyl/": TEST_PAGE}

    store = Store()
    report = search_web("Gorenje RK4182PS4 ljudnivå", PRODUCTS, OtherForm([TEST, FORUM]), Worker(JUDGED),
                        store.web_store(), rerank)
    assert "Vi mätte" in report.text and {r.url: r.raw for r in store.searches[0][2]}[TEST.url] == TEST_PAGE

    class Broken(Client):
        def extract(self, urls):
            raise WebSearchUnavailable("Tavily answered 432")

    store = Store()
    report = search_web("Gorenje RK4182PS4 ljudnivå", PRODUCTS, Broken([TEST, FORUM]), Worker(JUDGED),
                        store.web_store(), rerank)
    assert report.credits == 1 and len(store.searches) == 1  # the results are kept, so no new search is paid
    assert "Min Gorenje RK4182PS4 är tyst." in report.text
    assert "Tavily could not get the text of 1 relevant pages: Tavily answered 432." in report.text


def test_the_best_parts_go_to_the_chat_model_with_their_neighbours():
    page = "\n\n".join(["Gorenje RK4182PS4 i test.", FILLER, FILLER, "Vi mätte ljudnivån till 39 dB i vårt labb.",
                        FILLER, FILLER, "Bomann KG 320 är billigare men låter mer."])
    report, *_ = run_search([TEST, FORUM, SHOP], {TEST.url: page}, top=2)
    text = report.text
    assert "[web page 1] testsite.se, test, about id 2, 3, published 2025-03-08" in text
    assert "(best part 1) Vi mätte ljudnivån till 39 dB i vårt labb." in text
    assert "(best part 2) Min Gorenje RK4182PS4 är tyst." in text
    assert "Lorem ipsum" in text  # the parts before and after the hit
    assert "Bomann KG 320 är billigare" not in text  # two parts away from the hit, and not among the best
    assert "answer 'ljudnivå' best" in text  # the brand and the model are not in the question of the reranker
    assert "- elgiganten.se, shop, about id 2, published not known: Gorenje RK4182PS4" in text
    assert "not relevant, only a price" in text


def test_choose_parts_takes_the_best_by_rank_until_the_limit_in_the_page_order():
    parts = [Part(0, n, f"p{n}", {1: 0.9, 5: 0.8}.get(n, 0.1 - n / 100)) for n in range(8)]
    chosen = choose_parts(parts, top=2)
    assert [p.n for p in chosen] == [0, 1, 2, 4, 5, 6] and [p.rank for p in chosen] == [None, 1, None, None, 2, None]
    assert [p.n for p in choose_parts(parts, top=2, limit=4)] == [0, 1, 2]
    assert len(choose_parts(parts, top=1)) == 3
    assert MAX_CHAT_PARTS == 40
    # A hit that does not fit is skipped, and a later hit next to the chosen parts still comes in
    parts = [Part(0, n, f"p{n}", {1: 0.9, 5: 0.8, 2: 0.7}.get(n, 0.1 - n / 100)) for n in range(8)]
    assert [p.n for p in choose_parts(parts, top=3, limit=4)] == [0, 1, 2, 3]


def test_the_question_of_the_reranker_has_no_brand_and_no_model():
    gorenje = [ProductRef(2, "Gorenje kyl", "RK4182PS4", "Gorenje")]
    assert topic_question("Gorenje RK 4182 PS4 test buller", gorenje) == "test buller"
    assert topic_question("gorenje rk4182ps4 är den tyst?", gorenje) == "är den tyst?"
    assert topic_question("Bomann KG320XL ljud", []) == "Bomann ljud"  # a model number without a product
    assert topic_question("RK4182PS4", gorenje) == "RK4182PS4"  # nothing left: the question as it is


def test_want_ranks_the_parts_in_place_of_the_search_words():
    seen = []

    def spy(question, texts):
        seen.append(question)
        return rerank(question, texts)
    store = Store()
    search_web("Gorenje RK4182PS4 test", PRODUCTS, Client([FORUM]), Worker(JUDGED), store.web_store(), spy,
               want="is it quiet")
    assert seen == ["is it quiet"]


def test_a_relevant_page_whose_text_tavily_cannot_get():
    report, *_ = run_search([TEST, FORUM], {})
    assert "relevant; Tavily could not get its text" in report.text and report.credits == 1


def test_stored_results_of_the_question_cost_no_credit():
    stored = [WebResult(**{**vars(TEST), "raw": TEST_PAGE}), FORUM]
    report, store, client, _ = run_search([], store=Store(stored=stored))
    assert client.queries == [] and client.extracted == [] and report.credits == 0
    assert "stored results" in report.text and store.searches  # the floor counts the search again


def test_without_products_nothing_is_stored():
    report, store, *_ = run_search([TEST, FORUM], {TEST.url: TEST_PAGE}, products=[])
    assert store.chunks == {} and "39 dB" in report.text


def test_the_page_limit_and_a_worker_that_stops():
    store, client = Store(), Client([TEST, FORUM, SHOP])
    worker = Worker({**JUDGED, FORUM.url: LLMUnavailable("quota")})
    report = search_web("q", PRODUCTS, client, worker, store.web_store(), rerank, max_pages=2)
    assert report.pages == 2
    assert "1 more pages were found and not judged" in report.text
    assert "worker model is not available" in report.text


def tavily(handler):
    return TavilyClient("key", http=httpx.Client(transport=httpx.MockTransport(handler)))


def test_the_tavily_requests_and_their_results():
    sent = []

    def handler(request):
        sent.append(request)
        if request.url.path == "/search":
            return httpx.Response(200, json={"results": [{"url": "https://a.se/x", "title": "T", "content": "c",
                                                          "raw_content": None, "score": 0.9,
                                                          "published_date": "Sat, 08 Mar 2025 00:00:00 GMT"}]})
        return httpx.Response(200, json={"results": [{"url": "https://a.se/x", "raw_content": "full text"}],
                                         "failed_results": [{"url": "https://b.se/"}]})
    client = tavily(handler)
    assert client.search("kyl test") == [WebResult("https://a.se/x", "T", "c", "", "2025-03-08")]
    assert client.extract(["https://a.se/x", "https://b.se/"]) == {"https://a.se/x": "full text"}
    assert sent[0].headers["Authorization"] == "Bearer key"
    body = json.loads(sent[0].content)
    assert body["query"] == "kyl test" and body["include_raw_content"] == "text" and body["include_published_date"]
    assert json.loads(sent[1].content)["urls"] == ["https://a.se/x", "https://b.se/"]


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

    def search_web(self, question, ids, max_pages, want=""):
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
    assert shop.web_calls == [("RK4182 test", [2], 120)]
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
    week = now - timedelta(days=7)
    save_web_search(db, " Gorenje test ", [2], [vars(TEST)], week)
    first = db.execute("SELECT searched_at FROM web_searches").fetchone()["searched_at"]
    save_web_search(db, "gorenje test", [3, 2], [vars(TEST)], week)  # stored results used again: no new row
    rows = db.execute("SELECT listing_ids, searched_at FROM web_searches").fetchall()
    assert len(rows) == 1 and sorted(rows[0]["listing_ids"]) == [2, 3] and rows[0]["searched_at"] == first
    assert web_searched(db, [1, 2, 3], week) == {1, 2, 3}  # listing 1 is the same product as 2
    assert web_searched(db, [1], now + timedelta(minutes=1)) == set()
    assert stored_web_results(db, "gorenje TEST", now - timedelta(days=7))[0]["url"] == TEST.url
    assert stored_web_results(db, "other", now - timedelta(days=7)) is None
    finding = Finding(WebResult(**{**vars(TEST), "raw": TEST_PAGE}), "test", True, [1])
    ids = []
    for _ in range(2):  # a second read of the same text keeps the chunks (and their embeddings)
        save_web_chunks(db, 1, url_ref(TEST.url), web_chunks("Fridge 2026\nmodel", finding), now)
        ids.append([r["id"] for r in db.execute("SELECT id FROM chunks WHERE source = 'web'").fetchall()])
    assert len(ids[0]) == 1 and ids[0] == ids[1]
    changed = Finding(WebResult(**{**vars(TEST), "raw": "New text."}), "test", True, [1])
    save_web_chunks(db, 1, url_ref(TEST.url), web_chunks("Fridge", changed), now)  # new text replaces the old
    assert [r["text"].split("\n")[-1] for r in db.execute("SELECT text FROM chunks").fetchall()] == ["New text."]
    save_web_chunks(db, 1, url_ref(TEST.url), web_chunks("Fridge 2026\nmodel", finding), now)
    # without the two lines that name the product and the page; the chunks join the paragraphs with one newline
    assert web_texts(db, [2]) == {2: TEST_PAGE.replace("\n\n", "\n")}
