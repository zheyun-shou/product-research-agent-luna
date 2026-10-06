"""The chat behind the web page: one turn at a time, the events of a turn, the login pause and the delete button."""
import contextlib
import queue
import threading
from typing import Any, Callable, Iterator

from scout.agent.graph import Deps
from scout.agent.history import assistant
from scout.picks.write import attribute_text

PROBLEM_WAIT = 15 * 60  # seconds a turn waits for Done or Skip, then it skips
SHOWN_ATTRIBUTES = 4


class Busy(Exception):
    """A turn is running, so the service cannot start another one or change the state."""


class ChatService:
    """`build(say, on_problem)` gives the graph and its dependencies. The service gives the two callbacks, so the
    crawl report and the login pause of a turn go to the page that sent the message."""

    def __init__(self, build: Callable[[Callable, Callable], tuple[Any, Deps]],
                 end_transaction: Callable[[], None] = lambda: None):
        self._lock = threading.Lock()
        self._send: Callable[[dict], None] | None = None
        self._problem: dict | None = None
        self._end = end_transaction
        self.graph, self.deps = build(self._say, self._on_problem)

    @staticmethod
    def _config(thread: str) -> dict:
        return {"configurable": {"thread_id": thread}}

    def _values(self, thread: str) -> dict:
        return self.graph.get_state(self._config(thread)).values

    @contextlib.contextmanager
    def _idle(self):
        if not self._lock.acquire(blocking=False):
            raise Busy
        try:
            yield
        finally:
            self._lock.release()

    def _say(self, text: str) -> None:
        if self._send:
            self._send({"type": "status", "text": text})

    def _on_problem(self, status, url: str) -> bool:
        """Called by the crawl in the turn thread. True: the shopper dealt with it in the browser window."""
        done = threading.Event()
        self._problem = {"done": done, "skip": True}
        if self._send:
            self._send({"type": "problem", "detail": status.detail, "url": url})
        done.wait(PROBLEM_WAIT)
        skip, self._problem = self._problem["skip"], None
        return not skip

    def answer_problem(self, skip: bool) -> bool:
        """The Done or Skip button. False when no turn waits."""
        problem = self._problem
        if problem is None:
            return False
        problem["skip"] = skip
        problem["done"].set()
        return True

    def turn(self, thread: str, text: str) -> Iterator[dict]:
        """Start a turn and give its events. Raises Busy at once when a turn runs."""
        if not self._lock.acquire(blocking=False):
            raise Busy
        events: queue.Queue = queue.Queue()
        self._send = events.put

        def work():
            try:
                self._run(thread, text, events.put)
            finally:
                self._send = None
                self._lock.release()
                events.put(None)

        threading.Thread(target=work, daemon=True).start()
        return iter(events.get, None)

    def _run(self, thread: str, text: str, send: Callable[[dict], None]) -> None:
        config = self._config(thread)
        before = len(self._values(thread).get("messages", []))
        try:
            with self.deps.tracer.turn(thread, text) as span:
                state = self.graph.invoke({"messages": [{"role": "user", "content": text}]}, config)
                replies = [m["content"] for m in state["messages"][before + 1:]]
                span.update(output="\n\n".join(replies))
            for reply in replies:
                send({"type": "reply", "text": reply})
        except Exception as exc:  # the nodes catch most problems; this keeps the conversation open
            message = f"Something went wrong ({type(exc).__name__}: {exc}). You can try again."
            self._end()
            with contextlib.suppress(Exception):  # the thread gets the message, and the failed turn ends
                self.graph.update_state(config, {"messages": [assistant(message)]}, as_node="recommend")
            send({"type": "error", "text": message})
        self._end()
        send({"type": "picks", **self._picks(thread)})
        send({"type": "done"})

    def history(self, thread: str) -> dict:
        with self._idle():
            messages = [{"role": m["role"], "content": m["content"]}
                        for m in self._values(thread).get("messages", []) if m.get("role") in ("user", "assistant")]
            return {"thread": thread, "messages": messages, **self._picks(thread)}

    def remove(self, thread: str, product_id: int) -> dict:
        """The delete button: the same records as remove_picks, without an LLM call. The panel does not refill."""
        with self._idle():
            values = self._values(thread)
            picks = values.get("picks", [])
            gone = next((p for p in picks if p["product_id"] == product_id), None)
            if gone is None:
                raise KeyError(product_id)
            self.deps.eliminate(thread, [product_id], "deleted on the web page")
            removed = [*values.get("removed", []), {k: gone[k] for k in ("product_id", "listing_id", "title")}]
            message = f"Removed: {gone['title'][:60]}. It stays out for the rest of this conversation."
            self.graph.update_state(self._config(thread), {
                "picks": [p for p in picks if p is not gone], "removed": removed,
                "messages": [assistant(message)]}, as_node="recommend")
            self._end()
            return {"message": message, **self._picks(thread)}

    def _picks(self, thread: str) -> dict:
        values = self._values(thread)
        return {"picks": self.pick_views(values.get("picks", [])),
                "removed": [{"product_id": r["product_id"], "title": r["title"]} for r in values.get("removed", [])]}

    def pick_views(self, picks: list[dict]) -> list[dict]:
        """What the panel shows of each pick. All values come from the cards in the database."""
        if not picks:
            return []
        ids = [p["listing_id"] for p in picks]
        cards = {c.listing_id: c for c in self.deps.cards(ids)}
        texts = self.deps.page_texts(ids) if self.deps.page_texts else {}
        views = []
        for n, pick in enumerate(picks, 1):
            card = cards.get(pick["listing_id"])
            if card is None:
                continue
            doubtful = set(pick.get("doubtful", []))
            names = sorted(card.attributes, key=lambda name: name not in doubtful)[:SHOWN_ATTRIBUTES]
            images = (texts.get(card.listing_id) or {}).get("images") or []
            views.append({
                "number": n,
                "product_id": card.product_id,
                "listing_id": card.listing_id,
                "title": card.title,
                "url": card.url,
                "price": f"{card.price} {card.currency or ''}".strip() if card.price is not None else None,
                "rating": str(card.rating) if card.rating is not None else None,
                "review_count": card.review_count,
                "in_stock": card.in_stock,
                "image": next((u for u in images if u.startswith("https://")), None),
                "attributes": [{"name": name.replace("_", " "), "text": attribute_text(card.attributes[name]),
                                "doubtful": name in doubtful} for name in names],
                "note": pick.get("note"),
            })
        return views
