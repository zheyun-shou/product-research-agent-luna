from types import SimpleNamespace

import httpx2
import pytest
from openai import BadRequestError, InternalServerError, RateLimitError
from pydantic import BaseModel, ValidationError

from scout.llm.client import LLM, LLMOutputError, LLMUnavailable, QuotaExhausted, inline_refs, strip_fences
from scout.llm.config import LLMConfig, load_llm_config
from scout.llm.ratelimit import RateLimiter


class Inner(BaseModel):
    name: str


class Outer(BaseModel):
    items: list[Inner]


class Answer(BaseModel):
    answer: str


class FakeCompletions:
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=reply))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        )


class FakeOpenAI:
    def __init__(self, replies):
        self.chat = SimpleNamespace(completions=FakeCompletions(replies))


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.slept = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


CONFIG = LLMConfig.model_validate({
    "providers": {"gemini": {"base_url": "https://example.test/", "api_key_env": "GEMINI_API_KEY"}},
    "roles": {
        "chat": {"provider": "gemini", "model": "strong-model", "rpm": 60},
        "worker": {"provider": "gemini", "model": "light-model", "rpm": 60},
    },
})


def rate_limited(message):
    response = httpx2.Response(429, request=httpx2.Request("POST", "https://example.test/"))
    return RateLimitError(message, response=response, body=None)


def make(replies):
    fake, clock = FakeOpenAI(replies), FakeClock()
    return LLM(CONFIG, clients={"gemini": fake}, sleep=clock.sleep, clock=clock.clock), fake, clock


def test_structured_reply_uses_the_role_model():
    llm, fake, _ = make(['{"answer": "pong"}'])
    assert llm.structured("worker", "sys", "user", Answer) == Answer(answer="pong")
    assert fake.chat.completions.requests[0]["model"] == "light-model"
    assert llm.calls[0].input_tokens == 10


def test_fenced_json_is_accepted():
    llm, _, _ = make(['```json\n{"answer": "pong"}\n```'])
    assert llm.structured("chat", "sys", "user", Answer).answer == "pong"


def test_invalid_reply_is_retried_with_feedback():
    llm, fake, _ = make(['{"wrong": 1}', '{"answer": "pong"}'])
    assert llm.structured("chat", "sys", "user", Answer).answer == "pong"
    second = fake.chat.completions.requests[1]["messages"]
    assert second[-1]["role"] == "user" and "did not match" in second[-1]["content"]


def test_three_invalid_replies_raise():
    llm, _, _ = make(["nope", "nope", "nope"])
    with pytest.raises(LLMOutputError):
        llm.structured("chat", "sys", "user", Answer)


def test_per_minute_limit_waits_and_retries():
    llm, _, clock = make([rate_limited("GenerateRequestsPerMinutePerProjectPerModel-FreeTier"), '{"answer": "ok"}'])
    assert llm.structured("worker", "sys", "user", Answer).answer == "ok"
    assert 15 in clock.slept


def test_daily_limit_raises_quota_exhausted():
    llm, _, _ = make([rate_limited("GenerateRequestsPerDayPerProjectPerModel-FreeTier")])
    with pytest.raises(QuotaExhausted):
        llm.structured("worker", "sys", "user", Answer)


def test_rate_limited_on_every_try_also_raises_quota_exhausted():
    minute = "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"
    llm, _, _ = make([rate_limited(minute), rate_limited(minute), rate_limited(minute)])
    with pytest.raises(QuotaExhausted, match="still rate limited"):
        llm.structured("worker", "sys", "user", Answer)


def server_busy():
    response = httpx2.Response(503, request=httpx2.Request("POST", "https://example.test/"))
    return InternalServerError("This model is currently experiencing high demand.", response=response, body=None)


def test_busy_server_waits_and_retries():
    llm, _, clock = make([server_busy(), '{"answer": "ok"}'])
    assert llm.structured("chat", "sys", "user", Answer).answer == "ok"
    assert 15 in clock.slept


def test_busy_server_on_every_try_raises_unavailable():
    llm, _, _ = make([server_busy(), server_busy(), server_busy()])
    with pytest.raises(LLMUnavailable, match="busy or unreachable"):
        llm.structured("chat", "sys", "user", Answer)


def test_quota_exhausted_is_a_kind_of_unavailable():
    assert issubclass(QuotaExhausted, LLMUnavailable)


def test_rejected_schema_falls_back_to_json_mode():
    bad = BadRequestError("Invalid JSON schema", response=httpx2.Response(
        400, request=httpx2.Request("POST", "https://example.test/")), body=None)
    llm, fake, _ = make([bad, '{"answer": "pong"}'])
    assert llm.structured("worker", "sys", "user", Answer).answer == "pong"
    retry = fake.chat.completions.requests[1]
    assert retry["response_format"] == {"type": "json_object"}
    assert "schema" in retry["messages"][0]["content"]


def test_missing_key_names_the_variable(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    llm = LLM(CONFIG)
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        llm.structured("chat", "sys", "user", Answer)


def test_inline_refs_removes_refs():
    schema = inline_refs(Outer.model_json_schema())
    assert "$defs" not in schema
    assert schema["properties"]["items"]["items"]["properties"]["name"]["type"] == "string"


def test_strip_fences():
    assert strip_fences('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert strip_fences('{"a": 1}') == '{"a": 1}'


def test_rate_limiter_spaces_calls():
    clock = FakeClock()
    limiter = RateLimiter(30, sleep=clock.sleep, clock=clock.clock)
    limiter.wait()
    limiter.wait()
    assert clock.slept == [pytest.approx(2.0)]


def test_config_needs_both_roles_and_known_providers():
    with pytest.raises(ValidationError):
        LLMConfig.model_validate({"providers": {}, "roles": {"chat": {"provider": "x", "model": "m", "rpm": 1}}})


def test_shipped_config_loads():
    config = load_llm_config()
    assert {"chat", "worker"} <= set(config.roles)


FALLBACK_CONFIG = LLMConfig.model_validate({
    "providers": {"gemini": {"base_url": "https://example.test/", "api_key_env": "GEMINI_API_KEY"},
                  "groq": {"base_url": "https://groq.test/", "api_key_env": "GROQ_API_KEY"}},
    "roles": {
        "chat": {"provider": "gemini", "model": "strong-model", "rpm": 60,
                 "fallback": {"provider": "groq", "model": "backup-model", "rpm": 60}},
        "worker": {"provider": "gemini", "model": "light-model", "rpm": 60},
    },
})


def make_with_fallback(main_replies, backup_replies):
    main, backup, clock = FakeOpenAI(main_replies), FakeOpenAI(backup_replies), FakeClock()
    llm = LLM(FALLBACK_CONFIG, clients={"gemini": main, "groq": backup}, sleep=clock.sleep, clock=clock.clock)
    return llm, main, backup


def test_daily_quota_moves_the_role_to_its_fallback_for_the_rest_of_the_run():
    llm, main, backup = make_with_fallback(
        [rate_limited("GenerateRequestsPerDayPerProjectPerModel-FreeTier")],
        ['{"answer": "one"}', '{"answer": "two"}'])
    assert llm.structured("chat", "sys", "user", Answer).answer == "one"
    assert llm.structured("chat", "sys", "user", Answer).answer == "two"
    assert len(main.chat.completions.requests) == 1
    assert [r["model"] for r in backup.chat.completions.requests] == ["backup-model", "backup-model"]
    assert llm.model_for("chat") == "backup-model"
    assert "daily quota" in llm.fallbacks["chat"]
    assert [c.model for c in llm.calls] == ["backup-model", "backup-model"]


def test_busy_server_also_moves_to_the_fallback():
    llm, _, _ = make_with_fallback([server_busy()] * 3, ['{"answer": "ok"}'])
    assert llm.structured("chat", "sys", "user", Answer).answer == "ok"


def test_output_errors_do_not_move_to_the_fallback():
    llm, _, backup = make_with_fallback(["nope", "nope", "nope"], [])
    with pytest.raises(LLMOutputError):
        llm.structured("chat", "sys", "user", Answer)
    assert backup.chat.completions.requests == [] and llm.fallbacks == {}


def test_role_without_fallback_still_raises():
    llm, _, _ = make_with_fallback([rate_limited("GenerateRequestsPerDayPerProjectPerModel-FreeTier")], [])
    with pytest.raises(QuotaExhausted):
        llm.structured("worker", "sys", "user", Answer)


def test_missing_fallback_key_raises_unavailable_with_the_variable(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    main, clock = FakeOpenAI([rate_limited("GenerateRequestsPerDayPerProjectPerModel-FreeTier")]), FakeClock()
    llm = LLM(FALLBACK_CONFIG, clients={"gemini": main}, sleep=clock.sleep, clock=clock.clock)
    with pytest.raises(LLMUnavailable, match="GROQ_API_KEY"):
        llm.structured("chat", "sys", "user", Answer)


def test_groq_daily_limit_message_raises_quota_exhausted():
    message = ("Rate limit reached for model `openai/gpt-oss-120b` in organization `org` service tier `on_demand` "
               "on tokens per day (TPD): Limit 200000, Used 199990, Requested 500.")
    llm, _, _ = make([rate_limited(message)])
    with pytest.raises(QuotaExhausted):
        llm.structured("chat", "sys", "user", Answer)


def test_fallback_provider_must_exist():
    with pytest.raises(ValidationError):
        LLMConfig.model_validate({"providers": {"gemini": {"base_url": "x", "api_key_env": "K"}}, "roles": {
            "chat": {"provider": "gemini", "model": "m", "rpm": 1,
                     "fallback": {"provider": "groq", "model": "b", "rpm": 1}},
            "worker": {"provider": "gemini", "model": "m", "rpm": 1}}})
