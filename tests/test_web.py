import dataclasses
import json
import queue
import threading

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from scout.agent.clarify import CategoryChoice
from scout.agent.graph import build_graph
from scout.sources.base import SessionStatus
from scout.web.service import Busy, ChatService
from tests.fakes import FakeLLM
from tests.test_graph import World, ready


def service(world, llm, problem=False, texts=None):
    def build(say, on_problem):
        deps = dataclasses.replace(world.deps(llm), say=say,
                                   page_texts=lambda ids: {i: v for i, v in (texts or {}).items() if i in ids})
        if problem:
            crawl = deps.crawl

            def crawl_with_login(profile, needs, page):
                on_problem(SessionStatus(ok=False, problem="signin", detail="Sign-in page"), "https://www.amazon.se/x")
                return crawl(profile, needs, page)
            deps.crawl = crawl_with_login
        return build_graph(deps, InMemorySaver()), deps
    return ChatService(build)


def test_a_turn_gives_status_reply_and_picks_events():
    world = World(stored=3)
    texts = {1: {"images": ["data:x", "https://m.media-amazon.com/1.jpg"]}}
    chat = service(world, FakeLLM([CategoryChoice(category="fridge"), ready()]), texts=texts)
    events = list(chat.turn("t1", "A fridge with a freezer"))
    kinds = [e["type"] for e in events]
    assert kinds[0] == "status" and kinds[-2:] == ["picks", "done"] and kinds.count("reply") == 2
    picks = events[-2]["picks"]
    assert [p["number"] for p in picks] == [1, 2] and picks[0]["image"] == "https://m.media-amazon.com/1.jpg"
    assert picks[0]["price"] == "5000 SEK" and picks[0]["rating"] == "4.5" and picks[0]["review_count"] == 100
    assert picks[0]["attributes"] == [{"name": "height", "text": "143.0", "doubtful": False}]
    assert picks[1]["image"] is None
    history = chat.history("t1")
    assert [m["role"] for m in history["messages"]] == ["user", "assistant", "assistant"]
    assert history["picks"] == picks


def test_delete_a_pick_without_the_llm():
    world = World()
    chat = service(world, FakeLLM([CategoryChoice(category="fridge"), ready()]))
    list(chat.turn("t1", "A fridge with a freezer"))
    result = chat.remove("t1", 1)
    assert world.removed["t1"] == {1}
    assert [p["product_id"] for p in result["picks"]] == [2] and result["picks"][0]["number"] == 1
    assert result["removed"] == [{"product_id": 1, "title": "Fridge 1"}]
    assert chat.history("t1")["messages"][-1]["content"].startswith("Removed: Fridge 1.")
    with pytest.raises(KeyError):
        chat.remove("t1", 1)


def test_the_login_pause_waits_for_done():
    world = World(stored=3)
    chat = service(world, FakeLLM([CategoryChoice(category="fridge"), ready()]), problem=True)
    assert not chat.answer_problem(skip=False)  # nothing waits yet
    events = chat.turn("t1", "A fridge with a freezer")
    with pytest.raises(Busy):
        chat.turn("t1", "again")
    with pytest.raises(Busy):
        chat.history("t1")
    first = next(e for e in events if e["type"] != "status")
    assert first == {"type": "problem", "problem": "signin", "detail": "Sign-in page", "url": "https://www.amazon.se/x"}
    threading.Timer(0.05, chat.answer_problem, kwargs={"skip": False}).start()
    rest = list(events)
    assert rest[-1] == {"type": "done"} and world.crawls
    assert chat.history("t1")["picks"]  # the lock is free again



def test_a_long_step_gives_alive_events(monkeypatch):
    monkeypatch.setattr("scout.web.service.ALIVE_EVERY", 0.01)
    events = queue.Queue()
    stream = ChatService._events(events)
    assert next(stream) == {"type": "alive"}  # nothing for a while
    events.put({"type": "reply", "text": "x"})
    events.put(None)
    assert [e for e in stream if e["type"] != "alive"] == [{"type": "reply", "text": "x"}]

def test_a_failed_turn_gives_an_error_and_the_thread_continues():
    world = World()
    chat = service(world, FakeLLM([]))  # no queued reply: the clarify node gets an IndexError
    events = list(chat.turn("t1", "A fridge"))
    assert [e["type"] for e in events][-2:] == ["picks", "done"]
    assert any(e["type"] in ("error", "reply") for e in events)
    assert chat.history("t1")["messages"][0] == {"role": "user", "content": "A fridge"}


def test_the_routes():
    from fastapi.testclient import TestClient

    from scout.web.app import create_app

    world = World()
    client = TestClient(create_app(service(world, FakeLLM([CategoryChoice(category="fridge"), ready()]))))
    assert client.get("/").status_code == 200
    assert client.get("/api/threads/bad%20id").status_code == 400
    assert client.get("/api/threads/t1").json() == {"thread": "t1", "messages": [], "lang": None, "picks": [],
                                                    "removed": []}
    with client.stream("POST", "/api/threads/t1/messages", json={"text": "A fridge with a freezer"}) as response:
        events = [json.loads(line) for line in response.iter_lines() if line]
    assert events[-1] == {"type": "done"} and len(events[-2]["picks"]) == 2
    assert client.post("/api/threads/t1/messages", json={"text": ""}).status_code == 422
    assert client.delete("/api/threads/t1/picks/1").json()["removed"] == [{"product_id": 1, "title": "Fridge 1"}]
    assert client.delete("/api/threads/t1/picks/1").status_code == 404
    assert client.post("/api/problem", json={"skip": True}).status_code == 404


def test_a_failed_picks_load_does_not_stop_the_worker():
    world = World()
    chat = service(world, FakeLLM([CategoryChoice(category="fridge"), ready()]))
    list(chat.turn("t1", "A fridge with a freezer"))
    cards = chat.deps.cards
    chat.deps.cards = lambda ids: 1 / 0
    events = list(chat.turn("t1", "Thanks"))  # the FakeLLM has no reply left, and the picks fail to load
    assert events[-1] == {"type": "done"} and events[-2]["type"] == "error"
    chat.deps.cards = cards
    assert chat.history("t1")["picks"] and chat.in_worker(lambda: "alive") == "alive"


def test_the_picks_show_before_the_reviews_are_read():
    world = World(stored=3)

    def build(say, on_problem):
        deps = dataclasses.replace(world.deps(FakeLLM([CategoryChoice(category="fridge"), ready()])), say=say,
                                   read_reviews=lambda picks: "Read the shop reviews of 2 picks.")
        return build_graph(deps, InMemorySaver()), deps
    events = list(ChatService(build).turn("t1", "A fridge with a freezer"))
    kinds = [e["type"] for e in events]
    first_picks = kinds.index("picks")
    review = next(i for i, e in enumerate(events) if e.get("text") == "Read the shop reviews of 2 picks.")
    assert first_picks < review
    assert kinds[-2:] == ["picks", "done"]


def test_the_agent_loop_on_the_web_page():
    from scout.agent.loop import build_agent_graph
    from tests.test_loop import NEEDS, PICKS, Shop
    from scout.agent.loop import ReadReviews, SearchShop
    shop = Shop()
    llm = FakeLLM([[NEEDS, SearchShop(words="kylskåp"), ReadReviews(ids=[1, 2])], [PICKS]])

    def build(say, on_problem):
        deps = shop.deps(llm)
        deps.say = say
        deps.page_texts = lambda ids: {}
        return build_agent_graph(deps, InMemorySaver()), deps
    chat = ChatService(build)
    events = list(chat.turn("t1", "A fridge at most 180 cm"))
    kinds = [e["type"] for e in events]
    assert "status" in kinds and "picks" in kinds and kinds[-1] == "done"
    picks = next(e for e in events if e["type"] == "picks")["picks"]
    assert [p["listing_id"] for p in picks] == [2, 1]
    assert chat.remove("t1", 2)["picks"][0]["listing_id"] == 1


def test_the_web_page_gets_each_step_of_the_agent_loop():
    from scout.agent.loop import build_agent_graph
    from tests.test_loop import NEEDS, PICKS, Shop
    from scout.agent.loop import SearchShop
    shop = Shop()
    llm = FakeLLM([[NEEDS, SearchShop(words="kylskåp")], [PICKS]])

    def build(say, on_problem):
        deps = shop.deps(llm)
        deps.say = say
        deps.page_texts = lambda ids: {}
        return build_agent_graph(deps, InMemorySaver()), deps
    events = [e for e in ChatService(build).turn("t1", "A fridge at most 180 cm") if e["type"] == "step"]
    titles = [e["title"] for e in events if e["state"] != "run"]
    assert titles == ["Record the needs", "Search amazon.se for 'kylskåp'",
                      "Check the picks: the price and stock now, the reviews, the web tests"]
    assert events[0]["kind"] == "think" and events[0]["title"] == "Read the message and plan the work"
    search = next(e for e in events if e["kind"] == "shop" and e["state"] == "done")
    assert search["used"]["calls"] == 2 and search["limits"]["calls"] == 25 and search["seconds"] >= 0
    assert all(len(e.get("detail", "")) <= 160 for e in events)


def test_the_memory_routes():
    from fastapi.testclient import TestClient

    from scout.agent.loop import build_agent_graph
    from scout.web.app import create_app
    from tests.test_memory import deps_with_memory

    def build(say, on_problem):
        deps, memory = deps_with_memory()
        memory.remember("fact", "The niche is 60 cm wide", "fridge", "t0")
        memory.remember("dislike", "No Bomann", None, "t0")
        return build_agent_graph(deps, InMemorySaver()), deps
    client = TestClient(create_app(ChatService(build)))
    items = client.get("/api/memory").json()
    assert [(m["id"], m["kind"], m["category"], m["date"]) for m in items] == [
        (2, "dislike", None, "2026-10-14"), (1, "fact", "fridge", "2026-10-14")]
    assert client.delete("/api/memory/1").json() == {"ok": True}
    assert client.delete("/api/memory/1").status_code == 404
    assert [m["id"] for m in client.get("/api/memory").json()] == [2]


def test_the_agent_loop_sends_the_language_of_the_turn():
    from scout.agent.loop import ReadReviews, SearchShop, build_agent_graph
    from tests.test_loop import NEEDS, PICKS, Shop

    shop = Shop()
    llm = FakeLLM([[NEEDS, SearchShop(words="kylskåp"), ReadReviews(ids=[2, 1])], [PICKS]])

    def build(say, on_problem):
        deps = dataclasses.replace(shop.deps(llm), say=say)
        return build_agent_graph(deps, InMemorySaver()), deps
    chat = ChatService(build)
    events = list(chat.turn("t1", "我想买一个不超过 180 厘米的冰箱"))
    assert {"type": "lang", "lang": "zh"} in events
    assert chat.history("t1")["lang"] == "zh"
    assert chat.remove("t1", 2)["message"].startswith("已删除")


def test_the_thread_list_has_the_newest_first_with_the_first_message_as_title():
    from fastapi.testclient import TestClient

    from scout.agent.loop import build_agent_graph
    from scout.web.app import create_app
    from tests.test_memory import deps_with_memory

    def build(say, on_problem):
        deps, _ = deps_with_memory()
        return build_agent_graph(deps, InMemorySaver()), deps
    service = ChatService(build)
    service.deps.llm = FakeLLM(["First answer.", "Second answer.", "More."])
    for thread, text in (("t1", "A quiet   fridge\nunder 180 cm"), ("t2", "Headphones " + "x" * 100), ("t1", "More?")):
        list(service.turn(thread, text))
    client = TestClient(create_app(service))
    rows = client.get("/api/threads").json()
    assert [(r["thread"], r["title"]) for r in rows] == [("t1", "A quiet fridge under 180 cm"),
                                                         ("t2", ("Headphones " + "x" * 100)[:80])]
    assert rows[0]["last"] > rows[1]["last"]
