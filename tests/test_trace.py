from scout.llm.client import LLM
from scout.trace import NoTracer, tracer_from_env
from tests.fakes import FakeTracer
from tests.test_llm import CONFIG, Answer, FakeClock, FakeOpenAI, server_busy


def test_without_keys_tracing_does_nothing(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    tracer = tracer_from_env()
    assert isinstance(tracer, NoTracer)
    with tracer.turn("t1", "hi") as turn:
        with tracer.generation("chat Answer", "m") as gen:
            gen.update(output="x")
        turn.update(output="y")


def make(replies, tracer):
    fake, clock = FakeOpenAI(replies), FakeClock()
    return LLM(CONFIG, clients={"gemini": fake}, sleep=clock.sleep, clock=clock.clock, tracer=tracer)


def test_each_attempt_is_one_generation_with_its_tokens():
    tracer = FakeTracer()
    make(['{"wrong": 1}', '{"answer": "pong"}'], tracer).structured("chat", "sys", "user", Answer)
    generations = [e for e in tracer.events if e[0] == "generation"]
    assert generations == [("generation", "chat Answer", "strong-model")] * 2
    usage = [e[2]["usage_details"] for e in tracer.events if e[0] == "update"]
    assert usage == [{"input": 10, "output": 5}] * 2


def test_a_failed_attempt_is_recorded_as_an_error():
    tracer = FakeTracer()
    make([server_busy(), '{"answer": "ok"}'], tracer).structured("chat", "sys", "user", Answer)
    assert ("error", "generation") in tracer.events


def test_each_call_records_its_schema_and_whether_the_reply_matched():
    llm = make(['{"wrong": 1}', '{"answer": "pong"}'], FakeTracer())
    llm.structured("chat", "sys", "user", Answer)
    assert [(c.schema, c.valid) for c in llm.calls] == [("Answer", False), ("Answer", True)]


def test_a_hung_request_stops_after_the_timeout(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    client = LLM(CONFIG).client("gemini")
    assert (client.timeout, client.max_retries) == (LLM.TIMEOUT, 0)
