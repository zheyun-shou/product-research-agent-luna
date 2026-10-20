import httpx
from langgraph.checkpoint.memory import InMemorySaver

from scout.agent.loop import SameProduct, SearchWeb, build_agent_graph
from scout.review_sources import site_query, sites
from scout.shop_reviews import feefo_reviews, feefo_sku, more_reviews, prisjakt_id, prisjakt_reviews
from scout.websearch import ProductRef, thread_pages, topic_question
from tests.fakes import FakeLLM
from tests.test_loop import NEEDS
from tests.test_websearch import WebShop

CONFIG = {"shops": ["prisjakt.nu", "elgiganten.se"], "forums": ["sweclockers.com/forum"],
          "tests": {"all": ["testfakta.se"], "headphones": ["rtings.com"]}}


def test_the_sites_of_a_kind_go_into_the_search_words_with_or():
    assert sites("tests", "headphones", CONFIG) == ["testfakta.se", "rtings.com"]
    assert sites("tests", "fridge", CONFIG) == ["testfakta.se"]
    assert site_query("Sony WH-1000XM6", "shops", None, CONFIG) == \
        "Sony WH-1000XM6 (site:prisjakt.nu OR site:elgiganten.se)"
    assert site_query("Sony WH-1000XM6", "any", None, CONFIG) == "Sony WH-1000XM6"
    assert site_query("q", "tests", "incense", {"tests": {"incense": []}}) == "q"


def test_the_sites_are_not_a_topic_for_the_reranker():
    product = ProductRef(1, "Sony WH-1000XM6", "WH-1000XM6", "Sony")
    assert topic_question("Sony WH-1000XM6 test (site:rtings.com OR site:soundguys.com)", [product]) == "test"
    assert topic_question("OR mode (wired) noise", [product]) == "OR mode (wired) noise"  # words of the shopper


def test_a_search_of_shops_or_forums_is_not_a_test_search():
    import re
    from scout.review_sources import is_forum, not_for_tests
    pattern = not_for_tests(CONFIG)
    assert re.search(pattern, site_query("q", "shops", None, CONFIG))
    assert re.search(pattern, site_query("q", "forums", None, CONFIG))
    assert not re.search(pattern, site_query("q", "tests", "headphones", CONFIG)) and not re.search(pattern, "q")
    assert is_forum("https://www.sweclockers.com/forum/trad/1-x", CONFIG)
    assert not is_forum("https://www.sweclockers.com/test/1-x", CONFIG)


class Response:
    def __init__(self, status, data):
        self.status_code, self.data = status, data

    def json(self):
        return self.data


def test_prisjakt_reviews_come_from_its_review_api_with_the_market():
    calls = []

    def post(url, json, headers, timeout):
        calls.append((url, json, headers["Referer"]))
        return Response(200, {"reviews": [
            {"market": "SE", "rating": 2, "text": "Plastigt  etui.", "createdAt": "2026-01-02T10:00:00Z"},
            {"market": "NO", "rating": 5, "text": ""}]})
    url = "https://www.prisjakt.nu/produkt.php?p=15415649"
    assert prisjakt_id(url) == "15415649" and prisjakt_id("https://www.prisjakt.nu/c/horlurar") is None
    assert prisjakt_reviews(url, post) == [{"title": "Prisjakt SE", "rating": 2, "date": "2026-01-02",
                                            "verified": False, "text": "Plastigt etui."}]
    assert calls == [("https://www.prisjakt.nu/api/products/15415649/reviews", {"pageSize": 50}, url)]


def test_elgiganten_reviews_come_from_feefo():
    seen = {}

    def get(url, params, headers, timeout):
        seen.update(params)
        return Response(200, {"reviews": [{"locale": "sv_SE", "last_updated_date": "2025-05-01T00:00:00Z",
                                           "products": [{"rating": {"rating": 1}, "review": "Is på bakväggen."}]}]})
    url = "https://www.elgiganten.se/product/vitvaror/kyl-frys/kylskap/gorenje-kylskap-ri4182e1/151900"
    assert feefo_sku(url) == "151900"
    assert feefo_reviews(url, get) == [{"title": "Feefo sv_SE", "rating": 1, "date": "2025-05-01", "verified": False,
                                        "text": "Is på bakväggen."}]
    assert seen["product_sku"] == "151900" and seen["merchant_identifier"] == "elgiganten-sweden"


def test_more_reviews_only_for_the_sites_with_a_reader_and_a_failure_gives_none():
    def broken(*args, **kwargs):
        raise httpx.ConnectError("down")
    def odd(*args, **kwargs):
        return Response(200, ["not", "a", "dict"])
    no_wait = lambda site: None  # noqa: E731
    assert more_reviews("https://www.netonnet.se/art/x/1.9293/", broken, broken, no_wait) == []
    assert more_reviews("https://www.prisjakt.nu/produkt.php?p=1", broken, broken, no_wait) == []
    assert more_reviews("https://www.prisjakt.nu/produkt.php?p=1", odd, odd, no_wait) == []


def test_thread_pages_are_the_same_thread_from_page_2():
    url = "https://www.sweclockers.com/forum/trad/1757274-sony"
    html = ('<a href="/forum/trad/1757274-sony?p=1">1</a><a href="/forum/trad/1757274-sony?p=3">3</a>'
            '<a href="/forum/trad/1757274-sony?p=2">2</a><a href="/forum/trad/999-other?p=2">x</a>'
            '<a href="/forum/trad/1757274-sony?p=4">4</a>')
    assert thread_pages(html, url) == [url + "?p=2", url + "?p=3"]
    assert thread_pages(html, url + "?p=2") == []  # only from the first page
    shop = "https://www.prisjakt.nu/produkt.php"  # ?p= is a product here, not a page
    assert thread_pages('<a href="/produkt.php?p=12345">x</a>', shop + "?p=999") == []
    assert thread_pages('<a href="/produkt.php?p=12345">x</a>', shop) == []


def test_search_web_with_sources_searches_the_sites_of_the_list():
    shop = WebShop()
    llm = FakeLLM([[NEEDS, SearchWeb(question="Gorenje RK4182 test", ids=[2], sources="tests")], "Done."])
    state = build_agent_graph(shop.deps(llm), InMemorySaver()).invoke(
        {"messages": [{"role": "user", "content": "A fridge, at most 180 cm"}]}, {"configurable": {"thread_id": "t"}})
    question = shop.web_calls[0][0]
    assert question.startswith("Gorenje RK4182 test (site:") and "site:testfakta.se" in question
    assert state["web_searches"] == [question]


def test_a_researcher_joins_another_listing_only_to_its_own_product():
    from scout.agent.researcher import refused
    assert refused(SameProduct(id=7, same_as=2), 2) is None and refused(SameProduct(id=2, same_as=7), 2) is None
    assert "only join another listing to your own product" in refused(SameProduct(id=5, same_as=7), 2)
    assert refused(SameProduct(id=2, same_as=2), 2)


def test_the_polite_gap_waits_also_before_the_first_request():
    from scout.shop_reviews import _last, polite
    waits, now = [], [100.0]
    _last.pop("gap.test", None)
    polite("gap.test", 3.0, waits.append, lambda: now[0])
    now[0] += 1.0
    polite("gap.test", 3.0, waits.append, lambda: now[0])
    assert waits == [3.0, 2.0]
