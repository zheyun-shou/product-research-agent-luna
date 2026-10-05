"""One calling interface for OpenAI-compatible endpoints (Gemini, Groq) with structured output."""
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Literal, TypeVar

from openai import APIConnectionError, BadRequestError, InternalServerError, OpenAI, RateLimitError
from pydantic import BaseModel, ValidationError

from scout.llm.config import LLMConfig, ModelConfig
from scout.llm.ratelimit import RateLimiter
from scout.trace import NoTracer

Role = Literal["chat", "worker"]
T = TypeVar("T", bound=BaseModel)


class LLMUnavailable(RuntimeError):
    """The provider cannot serve requests now: quota used up, server busy, or no connection."""


class QuotaExhausted(LLMUnavailable):
    """The provider's daily free quota is used up."""


class LLMOutputError(RuntimeError):
    """The model did not return output that matches the schema."""


@dataclass
class CallRecord:
    role: str
    model: str
    input_tokens: int
    output_tokens: int
    seconds: float
    schema: str = ""
    valid: bool = True  # False when the reply did not match the schema


def inline_refs(schema: dict) -> dict:
    """Replace $ref with the referenced definition, since some endpoints reject $ref."""
    defs = schema.get("$defs", {})

    def resolve(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return resolve(defs[node["$ref"].split("/")[-1]])
            return {key: resolve(value) for key, value in node.items() if key != "$defs"}
        if isinstance(node, list):
            return [resolve(item) for item in node]
        return node

    return resolve(schema)


def strip_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[1] if "\n" in stripped else ""
        stripped = stripped.rstrip()
        if stripped.endswith("```"):
            stripped = stripped[:-3]
    return stripped.strip()


class LLM:
    MAX_ATTEMPTS = 3
    TIMEOUT = 120  # seconds for one request

    def __init__(self, config: LLMConfig, clients: dict[str, Any] | None = None, sleep=time.sleep,
                 clock=time.monotonic, tracer=None):
        self.config = config
        self._clients = dict(clients or {})
        self._sleep = sleep
        self._clock = clock
        self._limiters: dict[tuple[str, str], RateLimiter] = {}
        self.calls: list[CallRecord] = []
        self.fallbacks: dict[str, str] = {}  # role -> why it moved to its fallback model
        self._tracer = tracer or NoTracer()

    def _target(self, role: Role) -> ModelConfig:
        role_cfg = self.config.roles[role]
        return role_cfg.fallback if role in self.fallbacks else role_cfg

    def model_for(self, role: Role) -> str:
        return self._target(role).model

    def client(self, provider: str):
        if provider not in self._clients:
            settings = self.config.providers[provider]
            key = os.environ.get(settings.api_key_env)
            if not key:
                raise RuntimeError(f"{settings.api_key_env} is not set. Put it in .env in the project root.")
            # A hung request counts as unavailable after TIMEOUT seconds. _structured does its own retries.
            self._clients[provider] = OpenAI(base_url=settings.base_url, api_key=key, timeout=self.TIMEOUT,
                                             max_retries=0)
        return self._clients[provider]

    def structured(self, role: Role, system: str, user: str, schema: type[T]) -> T:
        fallback = self.config.roles[role].fallback
        if role not in self.fallbacks:
            try:
                return self._structured(role, self.config.roles[role], system, user, schema)
            except LLMUnavailable as exc:
                if fallback is None:
                    raise
                key_env = self.config.providers[fallback.provider].api_key_env
                if fallback.provider not in self._clients and not os.environ.get(key_env):
                    raise LLMUnavailable(f"{exc} The fallback {fallback.model} needs {key_env} in .env.") from exc
                self.fallbacks[role] = str(exc)
        return self._structured(role, fallback, system, user, schema)

    def _structured(self, role: Role, role_cfg: ModelConfig, system: str, user: str, schema: type[T]) -> T:
        client = self.client(role_cfg.provider)
        limiter = self._limiters.setdefault(
            (role_cfg.provider, role_cfg.model), RateLimiter(role_cfg.rpm, self._sleep, self._clock))
        schema_dict = inline_refs(schema.model_json_schema())
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        response_format = {"type": "json_schema", "json_schema": {"name": schema.__name__, "schema": schema_dict}}
        last_error = ""
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            limiter.wait()
            started = self._clock()
            try:
                with self._tracer.generation(
                    f"{role} {schema.__name__}", role_cfg.model, input=messages,
                    metadata={"provider": role_cfg.provider, "attempt": attempt},
                ) as gen:
                    response = client.chat.completions.create(
                        model=role_cfg.model, messages=messages, response_format=response_format
                    )
                    text = response.choices[0].message.content or ""
                    usage = getattr(response, "usage", None)
                    tokens_in = getattr(usage, "prompt_tokens", 0) or 0
                    tokens_out = getattr(usage, "completion_tokens", 0) or 0
                    gen.update(output=text, usage_details={"input": tokens_in, "output": tokens_out})
            except RateLimitError as exc:
                # Gemini says PerDay, Groq says "per day".
                if "perday" in str(exc).lower().replace(" ", ""):
                    raise QuotaExhausted(
                        f"The free daily quota for {role_cfg.model} is used up. It resets tomorrow."
                    ) from exc
                last_error = "rate limited"
                self._sleep(15 * attempt)
                continue
            except (InternalServerError, APIConnectionError):
                last_error = "unavailable"
                self._sleep(15 * attempt)
                continue
            except BadRequestError as exc:
                if response_format["type"] == "json_object":
                    raise
                # Some endpoints reject parts of a JSON schema. Fall back to JSON mode with the schema in the prompt.
                response_format = {"type": "json_object"}
                messages = [{"role": "system", "content": f"{system}\n\nReply with JSON that matches this "
                                                          f"schema:\n{json.dumps(schema_dict)}"}] + messages[1:]
                last_error = str(exc)
                continue
            record = CallRecord(role, role_cfg.model, tokens_in, tokens_out, self._clock() - started, schema.__name__)
            self.calls.append(record)
            try:
                return schema.model_validate_json(strip_fences(text))
            except ValidationError as exc:
                record.valid = False
                last_error = str(exc)
                messages = messages + [
                    {"role": "assistant", "content": text},
                    {"role": "user", "content": f"That reply did not match the JSON schema: {exc.errors()[:3]}. "
                                                "Reply again with JSON only."},
                ]
        if last_error == "rate limited":
            raise QuotaExhausted(f"{role_cfg.model} is still rate limited after {self.MAX_ATTEMPTS} tries.")
        if last_error == "unavailable":
            raise LLMUnavailable(f"{role_cfg.model} is busy or unreachable after {self.MAX_ATTEMPTS} tries.")
        raise LLMOutputError(
            f"{role_cfg.model} gave no valid {schema.__name__} after {self.MAX_ATTEMPTS} tries: {last_error}"
        )
