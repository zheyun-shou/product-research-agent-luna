"""Optional Langfuse tracing. Without the LANGFUSE keys, every call does nothing."""
import os
from contextlib import contextmanager
from typing import Any, Iterator


class NoSpan:
    def update(self, **fields: Any) -> None:
        pass


class NoTracer:
    enabled = False

    @contextmanager
    def turn(self, thread_id: str, text: str) -> Iterator[NoSpan]:
        yield NoSpan()

    @contextmanager
    def span(self, name: str, **fields: Any) -> Iterator[NoSpan]:
        yield NoSpan()

    @contextmanager
    def generation(self, name: str, model: str, **fields: Any) -> Iterator[NoSpan]:
        yield NoSpan()

    def flush(self) -> None:
        pass


class LangfuseTracer:
    """One trace for each chat turn. The traces of one thread form one Langfuse session."""

    enabled = True

    def __init__(self, client):
        self._client = client

    @contextmanager
    def turn(self, thread_id: str, text: str):
        from langfuse import propagate_attributes

        with self._client.start_as_current_observation(name="chat turn", as_type="agent", input=text) as span:
            with propagate_attributes(session_id=thread_id, trace_name="chat turn"):
                yield span

    @contextmanager
    def span(self, name: str, **fields: Any):
        with self._client.start_as_current_observation(name=name, as_type="span", **fields) as span:
            yield span

    @contextmanager
    def generation(self, name: str, model: str, **fields: Any):
        with self._client.start_as_current_observation(
            name=name, as_type="generation", model=model, **fields
        ) as gen:
            yield gen

    def auth_check(self) -> bool:
        return self._client.auth_check()

    def flush(self) -> None:
        self._client.flush()


def tracer_from_env():
    if os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY"):
        from langfuse import Langfuse

        return LangfuseTracer(Langfuse())  # LANGFUSE_BASE_URL selects the cloud region or a local server
    return NoTracer()
