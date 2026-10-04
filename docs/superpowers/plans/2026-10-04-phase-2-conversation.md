# Phase 2 Conversation Implementation Plan

> **How to use this plan.** Do the tasks in order. Each step has a checkbox (`- [ ]`). Mark the step when it is done. Commit at the end of each task.

**Goal:** Replace the one-shot `scout picks` command with a conversation in the terminal. The agent clarifies the needs of the user, searches amazon.se when the stored data is not enough, shows the picks, and handles follow-up turns: questions, calculations, rejected picks, changed needs and "show me more". The user can stop and continue a conversation later. Langfuse records each turn.

**Architecture:** A LangGraph `StateGraph` runs one turn for each user message. A Postgres checkpointer keeps the state of each thread. The state keeps only product IDs and the needs, never full records. Each node uses code from phase 1: the picks steps move into one module (`scout/picks/pipeline.py`) that the command and the graph share. The chat model selects the next action through structured output (one `Route` object per turn). Python validates each choice, does all filters and all arithmetic, and checks each number in each answer against the records. The worker model screens titles for the kind of product that the user wants and checks the sizes on each pick against the rest of the page.

**Tech Stack:** Phase 1 stack, plus `langgraph` 1.2, `langgraph-checkpoint-postgres` 3.1 (`PostgresSaver`) and `langfuse` 4.16. Claude installed these three on Python 3.10 in a scratch environment on 2026-10-04 and checked the APIs that this plan uses: `StateGraph`, conditional edges, nodes that take `(state, config)`, `InMemorySaver`, `PostgresSaver(conn).setup()`, `Langfuse.start_as_current_observation`, `propagate_attributes(session_id=...)`.

**Spec:** `docs/superpowers/specs/2026-10-02-product-scout-design.md`, sections 3, 4.7 to 4.10, and phase 2 in section 9.

**Video:** a walkthrough of this plan, with captions and voice, is at https://claude.ai/artifact/BRmWUvhE8E82DBwQQYeLNx. Fig. 4 of the diagram (https://claude.ai/artifact/4jJqCkVudUFU24kTyXsiqZ) shows one turn of the conversation.

**Dry run.** On 2026-10-04 Claude applied all code of this plan in a temporary Git worktree, on commit f676761. 204 tests passed: the 163 tests of phase 1 and 41 new ones. Then Claude ran one real clarify turn of `scout chat` in Chinese ("a fridge, 60 cm wide, 186 cm high, budget 9000 kronor"). The agent stored width<=60, height<=186 and budget 9000. It asked about the kind (with or without a freezer part), the depth and the noise. No browser opened. The chat role used 2 calls on Groq, because the Gemini chat quota was used up. Claude then deleted that thread and the worktree. Later on 2026-10-04, Zheyun added decisions 7 and 8. Claude applied the changed plan again in a new worktree: 207 tests passed (163 and 44 new). Task 12 was not part of either run. The tasks below are still not done in the repository.

## Global Constraints

- All constraints of the phase 1 plan stay. The most important ones for this phase:
  - Python 3.10. Price is only a budget filter. The LLM never writes SQL. The user logs in and solves captchas.
  - Each number that the agent shows comes from a record, from the user, or from a Python calculation. Python checks this before the agent shows the text.
  - Use only free tiers. Never enable billing on Gemini, Groq or Langfuse.
- The conversation state keeps product IDs, the needs and the messages. Tools get the product data from Postgres when a step needs it.
- A conversation that only clarifies the needs must not open the browser.
- Langfuse is optional. Without `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY`, all tracing calls do nothing, and the program prints no warning.
- The tests never call a real LLM, a real browser or Langfuse. They use `FakeLLM`, fake dependencies and `InMemorySaver`. Only tests with the mark `db` use the Postgres container.

## Decisions for this phase

These points differ from the spec or make it more exact. Task 14 writes them into the spec.

1. **Tool selection is structured output, not native tool calls.** Each follow-up turn makes one `Route` object with one action. This uses the same JSON path for Gemini and Groq, and Python validates each choice before a tool runs. The spec tools map to actions as follows. `query_products`: `change_needs`, then the SQL and Python filters. `exclude_and_rerank`: `exclude`. `fetch_more`: `more`. `calculate`: `calculate`. `recheck_availability`: `recheck`. `search_text` and `extract_attribute` need chunks and come in phase 3. `update_profile` comes in phase 4, with profile generation.
2. **Login and captcha pauses stay a prompt in the terminal.** The graph runs in the same process as the terminal, so the crawl can wait for the user, as in phase 1. The LangGraph interrupt comes in phase 7, when a web page replaces the terminal.
3. **Profiles get kinds.** The 5 l cosmetics cooler ranked second in the phase 1 runs, because "fridge" was too wide. A profile now lists kinds (for the fridge: with a freezer part, without one, mini fridge, wine cooler, cool box), each with Swedish search terms. The clarify step must know the kind before it searches. The title screening keeps only items of that kind.
4. **The clarify step must not invent numbers.** Each condition and the budget must agree with a number that the user wrote, with a unit change of a power of ten at most (1,8 m is 180 cm). Otherwise the model must ask.
5. **Size doubts are marked, never changed.** The worker compares the sizes on each pick with the title, the bullet points and the specifications. A doubtful size gets "(doubtful)" and a note. A quote in the note must be on the page.
6. **A hidden price comes from one click on the page.** Zheyun found two cases in her label review on 2026-10-04 (5 of 38 labelled pages). On 4 pages, amazon.se shows no buy box, only "Priset är högre än vanligt" and the button "Visa alla köpalternativ". The program opens the offer panel and reads the lowest new offer that can go in the cart. On 1 page, the price shows only after a size is selected. The program clicks each size and stores each one as a variant. Each price keeps its source (`all_offers` or `size_choice`), and the picks show it.
7. **Each thread searches one time before it uses the stored products.** Zheyun asked on 2026-10-04: stored data gets old. So the first search for a set of search terms in a thread always reads result page 1 on amazon.se. Products that are already stored get the rating and the review count of the result card, and the browser does not open them. Only new products get a product page. When all results are already stored, the search costs one page load. After that, the thread uses the stored products while 10 or more meet the needs.
8. **A failed check tells the model what is wrong and what is right.** Zheyun asked for this on 2026-10-04. The next round gets the last answer of the model and each problem with the value from the record, for example "height is 185, not 186", or the numbers on the card for a number that has no claim. After the last round, only the picks that still fail lose their summary.

## Review Focus

1. The clarify step gets "höjd 186" and returns height 185. Python must send it back, and the final needs must say 186. Test in Task 6.
2. A follow-up answer has a number that is not in its claims, not in the user's messages and not a pick number. The agent must not show it. Test in Task 7.
3. The user rejects pick 1. Pick 1 must stay out for the rest of the thread, also after a restart. Tests in Tasks 4, 9 and 10.
4. The calculation tool gets an exchange rate that the user did not give. It must ask for the rate and calculate nothing. Test in Task 8.
5. A conversation that only clarifies never calls the crawl and never opens the browser. Test in Task 9.
6. The size check returns a quote that is not on the page. The size must stay as it is, with no note. Test in Task 11.
7. A product page has no buy box and the offer panel has only used offers. The price must stay empty. A used offer must never become the price. Test in Task 12.
8. A new thread starts with 20 stored products that meet the needs. The agent must still read result page 1 one time, and it must not open the product pages of stored products. Tests in Tasks 4 and 9.
9. A summary has a number that is not in its claims. The retry prompt must contain the last answer and the numbers on that card. After three failed rounds, the other picks keep their summaries. Test in Task 3.

---

## File Structure

```
pyproject.toml                       add langgraph, langgraph-checkpoint-postgres, langfuse
profiles/fridge.yaml                 add search_terms_sv and kinds
src/scout/
  trace.py                           NoTracer, LangfuseTracer, tracer_from_env
  llm/feedback.py                    retry_prompt: the last answer and each problem
  needs.py                           Needs, ConditionSpec, check_needs, was_said
  profiles.py                        add ProductKind, CategoryProfile.kinds and .kind()
  crawl.py                           add first_page, skip_known, CrawlReport.known
  cli.py                             picks uses the pipeline; add chat, threads, trace-ping
  llm/client.py                      a tracer records each attempt as a generation
  extract/screen.py                  screen_titles takes the wanted kind
  extract/doubt.py                   worker size check with checked quotes
  sources/amazon_se/offers.py        parse the offer panel (Visa alla köpalternativ)
  sources/amazon_se/product.py       detect a hidden price: offer panel or size choice
  db/schema.sql                      add eliminations
  db/conn.py                         add connect_checkpoints
  db/store.py                        add known_source_ids, refresh_listings, add_eliminations, eliminated_products
  picks/pipeline.py                  shortlist, make_picks, refetcher, PicksResult
  picks/rank.py                      add load_cards_by_ids
  picks/write.py                     shared claim checks; "(doubtful)" in the output
  agent/
    __init__.py
    state.py                         ChatState
    history.py                       history_text, user_numbers, compact
    clarify.py                       pick_category, clarify
    followup.py                      Route, choose_route, answer_question
    calc.py                          CalcPlan, run_plan, calculate
    graph.py                         Deps, build_graph
    live.py                          LazyBrowser, live_deps
tests/
  fakes.py                           add FakeTracer
  test_trace.py, test_needs.py, test_picks_pipeline.py, test_history.py, test_clarify.py,
  test_followup.py, test_calc.py, test_graph.py, test_chat_db.py, test_doubt.py
```

---

### Task 1: Dependencies and optional Langfuse tracing

**Files:**
- Modify: `pyproject.toml`
- Create: `src/scout/trace.py`
- Modify: `src/scout/llm/client.py`
- Modify: `tests/fakes.py`
- Create: `tests/test_trace.py`
- Modify: `src/scout/cli.py` (command `trace-ping`)

- [ ] **Step 1: Add the dependencies**

In `pyproject.toml`, add these lines to `dependencies`:

```toml
  "langfuse>=4.16,<5",
  "langgraph>=1.2,<2",
  "langgraph-checkpoint-postgres>=3.1,<4",
```

Run: `uv pip install -e ".[dev]"`
Expected: the three packages and their dependencies (`langchain-core`, `opentelemetry-sdk`, `psycopg-pool`) install.

- [ ] **Step 2: Write the failing tests**

Add to `tests/fakes.py`:

```python
from contextlib import contextmanager


class FakeSpan:
    def __init__(self, events, kind):
        self.events, self.kind = events, kind

    def update(self, **fields):
        self.events.append(("update", self.kind, fields))


class FakeTracer:
    """Records turns, spans and generations as tuples in events."""

    enabled = True

    def __init__(self):
        self.events = []

    @contextmanager
    def _record(self, event):
        self.events.append(event)
        try:
            yield FakeSpan(self.events, event[0])
        except Exception:
            self.events.append(("error", event[0]))
            raise

    def turn(self, thread_id, text):
        return self._record(("turn", thread_id))

    def span(self, name, **fields):
        return self._record(("span", name))

    def generation(self, name, model, **fields):
        return self._record(("generation", name, model))

    def flush(self):
        self.events.append(("flush",))
```

Create `tests/test_trace.py`:

```python
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
```

- [ ] **Step 3: Run the tests to see them fail**

Run: `python -m pytest tests/test_trace.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scout.trace'`.

- [ ] **Step 4: Write the tracer**

Create `src/scout/trace.py`:

```python
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
```

- [ ] **Step 5: Record each LLM attempt**

In `src/scout/llm/client.py`, import the tracer:

```python
from scout.trace import NoTracer
```

Add a `tracer` argument to `LLM.__init__`:

```python
    def __init__(self, config: LLMConfig, clients: dict[str, Any] | None = None, sleep=time.sleep,
                 clock=time.monotonic, tracer=None):
        ...
        self._tracer = tracer or NoTracer()
```

In `_structured`, replace the `try` block that calls `client.chat.completions.create` and the lines that read `text` and `usage` after it with this code. The `except` blocks stay as they are. A failed call raises inside the `with`, so the tracer records it as an error.

```python
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
                ...  # unchanged
            self.calls.append(CallRecord(role, role_cfg.model, tokens_in, tokens_out, self._clock() - started))
```

- [ ] **Step 6: Add `scout trace-ping`**

In `src/scout/cli.py`:

```python
from scout.trace import tracer_from_env


@app.command("trace-ping")
def trace_ping() -> None:
    """Check the Langfuse keys and send one test trace."""
    load_settings()  # loads .env
    tracer = tracer_from_env()
    if not tracer.enabled:
        typer.echo("LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are not set, so tracing is off.")
        raise typer.Exit(1)
    typer.echo(f"keys accepted: {tracer.auth_check()}")
    with tracer.turn("trace-ping", "ping") as span:
        span.update(output="pong")
    tracer.flush()
    typer.echo("Sent one trace named 'chat turn' in session 'trace-ping'.")
```

- [ ] **Step 7: Run all tests**

Run: `python -m pytest -q`
Expected: all tests pass, including the 3 new ones.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml src/scout/trace.py src/scout/llm/client.py src/scout/cli.py tests/fakes.py tests/test_trace.py
git commit -m "Optional Langfuse tracing: one generation per LLM attempt; trace-ping command"
```

---

### Task 2: Needs, product kinds and screening for a kind

**Files:**
- Modify: `src/scout/profiles.py`, `profiles/fridge.yaml`
- Create: `src/scout/needs.py`
- Modify: `src/scout/extract/screen.py`
- Create: `tests/test_needs.py`
- Modify: `tests/test_profiles.py`, `tests/test_screen.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_profiles.py`:

```python
def test_fridge_kinds_have_swedish_search_terms():
    profile = load_profile("fridge")
    assert profile.search_terms_sv == "kylskåp"
    assert profile.kind("fridge_freezer").search_terms_sv == "kyl och frys"
    assert profile.kind("nothing") is None and profile.kind(None) is None
```

Add to `tests/test_screen.py`:

```python
def test_the_wanted_kind_goes_into_the_prompt():
    llm = FakeLLM([Keep(keep=[0])])
    screen_titles([cand(1, "FOHERE minikylskåp 5 l")], load_profile("fridge"), llm, "Mini fridge for a room or an office")
    assert "The shopper wants: Mini fridge for a room or an office" in llm.requests[0][1]
```

Create `tests/test_needs.py`:

```python
from decimal import Decimal

from scout.needs import ConditionSpec, Needs, check_needs, was_said
from scout.profiles import load_profile

FRIDGE = load_profile("fridge")


def needs(**fields):
    return Needs(category="fridge", **fields)


def test_text_is_the_same_as_in_the_picks_command():
    n = needs(conditions=[ConditionSpec(attribute="height", op="<=", value=186)], budget=9000, preferences=["quiet"])
    assert n.text() == "height<=186, budget 9000 SEK, prefers quiet"
    assert n.budget_sek() == Decimal("9000")
    assert needs().text() == ""


def test_wanted_and_search_terms_follow_the_kind():
    assert needs(kind="mini_fridge").wanted(FRIDGE) == "Mini fridge for a room or an office"
    assert needs().wanted(FRIDGE) == "Fridge"
    assert needs(kind="mini_fridge").search_terms(FRIDGE) == "minikylskåp"
    assert needs().search_terms(FRIDGE) == "kylskåp"
    assert needs(kind="mini_fridge", search_terms_sv="liten kyl").search_terms(FRIDGE) == "liten kyl"


def test_unit_changes_count_as_said():
    said = {Decimal("1.8"), Decimal("9"), Decimal("600")}
    assert was_said(180, said)      # 1,8 m
    assert was_said(9000, said)     # 9k
    assert was_said(60, said)       # 600 mm
    assert not was_said(185, said)


def test_check_needs_finds_unknown_names_and_invented_numbers():
    n = needs(kind="freezer_room", budget=5000, conditions=[
        ConditionSpec(attribute="colour", op="<=", value=1),
        ConditionSpec(attribute="energy_class", op="<=", value=3),
        ConditionSpec(attribute="height", op="<=", value=186),
    ])
    errors = check_needs(n, FRIDGE, said={Decimal("186")})
    assert any("kind 'freezer_room'" in e for e in errors)
    assert any("'colour'" in e for e in errors)
    assert any("'energy_class' is not a number" in e for e in errors)
    assert any("budget 5000" in e for e in errors)
    assert not any("height" in e for e in errors)
    assert check_needs(n.model_copy(update={"kind": None, "conditions": [], "budget": None}), FRIDGE) == []
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `python -m pytest tests/test_needs.py tests/test_profiles.py tests/test_screen.py -v`
Expected: FAIL. `scout.needs` does not exist, and the profile has no `kinds`.

- [ ] **Step 3: Add kinds to the profile model**

In `src/scout/profiles.py`, add before `CategoryProfile`:

```python
class ProductKind(BaseModel):
    name: str
    label: str
    search_terms_sv: str
```

Add these fields and this method to `CategoryProfile`:

```python
    search_terms_sv: str | None = None
    kinds: list[ProductKind] = Field(default_factory=list)

    def kind(self, name: str | None) -> ProductKind | None:
        return next((k for k in self.kinds if k.name == name), None)
```

In `profiles/fridge.yaml`, add after `display_name`:

```yaml
search_terms_sv: kylskåp
kinds:
  - name: fridge_freezer
    label: Fridge with a freezer part
    search_terms_sv: kyl och frys
  - name: fridge
    label: Fridge without a freezer part
    search_terms_sv: kylskåp utan frys
  - name: mini_fridge
    label: Mini fridge for a room or an office
    search_terms_sv: minikylskåp
  - name: wine_cooler
    label: Wine cooler
    search_terms_sv: vinkyl
  - name: cool_box
    label: Cool box for a car or camping
    search_terms_sv: kylbox
```

Put this question first in `questions_for_user`:

```yaml
  - What kind of fridge do you want? With a freezer part, without one, a mini fridge, a wine cooler or a cool box?
```

- [ ] **Step 4: Write the needs model**

Create `src/scout/needs.py`:

```python
"""What the shopper wants: the kind of product, hard conditions, the budget and soft preferences."""
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from scout.picks.rank import Condition
from scout.profiles import CategoryProfile

# A number counts as said when the shopper wrote it, or wrote it in a unit that differs by a power of ten.
FACTORS = tuple(Decimal(f) for f in ("1", "10", "100", "1000", "0.1", "0.01", "0.001"))


class ConditionSpec(BaseModel):
    attribute: str
    op: Literal["<=", ">="]
    value: float


class Needs(BaseModel):
    category: str | None = None
    kind: str | None = None
    conditions: list[ConditionSpec] = Field(default_factory=list)
    budget: float | None = None  # SEK
    preferences: list[str] = Field(default_factory=list)
    search_terms_sv: str | None = None

    def budget_sek(self) -> Decimal | None:
        return None if self.budget is None else Decimal(str(self.budget))

    def rank_conditions(self) -> list[Condition]:
        return [Condition(c.attribute, c.op, c.value) for c in self.conditions]

    def text(self) -> str:
        parts = [f"{c.attribute}{c.op}{c.value:g}" for c in self.conditions]
        if self.budget is not None:
            parts.append(f"budget {self.budget:g} SEK")
        parts += [f"prefers {p}" for p in self.preferences]
        return ", ".join(parts)

    def wanted(self, profile: CategoryProfile) -> str:
        kind = profile.kind(self.kind)
        return kind.label if kind else profile.display_name

    def search_terms(self, profile: CategoryProfile) -> str:
        kind = profile.kind(self.kind)
        return self.search_terms_sv or (kind.search_terms_sv if kind else None) \
            or profile.search_terms_sv or profile.category


def was_said(value: float, said: set[Decimal]) -> bool:
    target = Decimal(str(value))
    return any(number * factor == target for number in said for factor in FACTORS)


def _said_text(said: set[Decimal]) -> str:
    return "the shopper wrote: " + (", ".join(f"{n:g}" for n in sorted(said)) or "no numbers")


def check_needs(needs: Needs, profile: CategoryProfile, said: set[Decimal] | None = None) -> list[str]:
    """Return the problems. With `said`, each number must be one that the shopper wrote."""
    errors = []
    if needs.kind is not None and profile.kind(needs.kind) is None:
        errors.append(f"kind '{needs.kind}' is not one of {[k.name for k in profile.kinds]}")
    for cond in needs.conditions:
        attr = profile.attribute(cond.attribute)
        if attr is None:
            errors.append(f"'{cond.attribute}' is not an attribute in the profile")
        elif attr.type != "number":
            errors.append(f"'{cond.attribute}' is not a number attribute, so it cannot have a condition")
        elif said is not None and not was_said(cond.value, said):
            errors.append(f"{cond.attribute} {cond.value:g} is not a number the shopper wrote "
                          f"({_said_text(said)}). Use one of them, or ask.")
    if needs.budget is not None:
        if needs.budget <= 0:
            errors.append("budget must be more than 0")
        elif said is not None and not was_said(needs.budget, said):
            errors.append(f"budget {needs.budget:g} is not a number the shopper wrote ({_said_text(said)}). "
                          "Use one of them, or ask.")
    return errors
```

- [ ] **Step 5: Screen for the wanted kind**

In `src/scout/extract/screen.py`, change `screen_titles`:

```python
def screen_titles(cands: list[T], profile: CategoryProfile, llm, wanted: str | None = None) -> list[T]:
    lines = "\n".join(f"{n}: {c.title}" for n, c in enumerate(cands))
    user = f"The shopper wants: {wanted or profile.display_name}\n\nResults:\n{lines}\n\nReturn the numbers to keep."
    keep = set(llm.structured("worker", SYSTEM, user, Keep).keep)
    return [c for n, c in enumerate(cands) if n in keep]
```

- [ ] **Step 6: Run all tests**

Run: `python -m pytest -q`
Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add profiles/fridge.yaml src/scout/profiles.py src/scout/needs.py src/scout/extract/screen.py tests/test_needs.py tests/test_profiles.py tests/test_screen.py
git commit -m "Needs model with checks against the user's numbers; product kinds in profiles; screening for a kind"
```

---

### Task 3: One picks pipeline for the command and the agent

**Files:**
- Modify: `src/scout/picks/write.py` (shared claim checks, feedback for the next round)
- Create: `src/scout/llm/feedback.py`
- Modify: `src/scout/picks/rank.py` (`load_cards_by_ids`)
- Create: `src/scout/picks/pipeline.py`
- Modify: `src/scout/cli.py` (`picks` uses the pipeline)
- Create: `tests/test_picks_pipeline.py`
- Modify: `tests/test_picks_write.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_picks_write.py`:

```python
def test_the_next_round_gets_the_last_answer_and_the_numbers_on_the_card():
    bad = picks("Only 30 dB, rated 4.9.", [("rating", "4.9")])
    good = picks("Rated 4.5.", [("rating", "4.5")])
    llm = FakeLLM([bad, good])
    write_picks(llm, list(cards().values()), "")
    retry = llm.requests[1][1]
    assert '"summary":"Only 30 dB, rated 4.9."' in retry
    assert "rating is 4.5, not 4.9" in retry
    assert "the number 30 in the summary is not in the claims" in retry
    assert "Numbers on this card: price=5000, rating=4.5, review_count=1234, height=178" in retry


def test_after_the_last_round_only_failed_picks_lose_their_summary():
    two = {**cards(), 2: card(2, 4.0, 50)}
    text = PicksText(picks=[PickText(listing_id=1, summary="Rated 4.9.", claims=[Claim(field="rating", value="4.9")]),
                            PickText(listing_id=2, summary="Rated 4.0.", claims=[Claim(field="rating", value="4.0")])])
    kept, errors = write_picks(FakeLLM([text, text, text]), list(two.values()), "")
    assert [p.listing_id for p in kept.picks] == [2] and errors[0] == "1: rating is 4.5, not 4.9"
    assert all(e.startswith("1:") for e in errors)
    out = format_picks(list(two.values()), kept, [], "", 0, errors)
    assert "Rated 4.0." in out and "Rated 4.9." not in out and "Some summaries did not pass" in out
```

The test card of `tests/test_picks_rank.py` has the price 5000. Check this before you run the test, and change the expected text if the card has another price.

Create `tests/test_picks_pipeline.py`:

```python
import pytest

from scout.extract.screen import Keep
from scout.needs import Needs
from scout.picks.pipeline import make_picks, shortlist
from scout.picks.rank import load_cards_by_ids
from scout.profiles import load_profile
from scout.sources.amazon_se.product import parse_product_page
from tests.fakes import FakeLLM
from tests.test_picks_db import FIXTURES, NOW, save

pytestmark = pytest.mark.db
FRIDGE = load_profile("fridge")
PAGES = {"B0AAAAAAA1": "product_full.html", "B0AAAAAAA3": "product_parts_price.html"}


def stock(db):
    for asin, name in PAGES.items():
        save(db, name, asin)


def refetch(card):
    html = (FIXTURES / PAGES[card.url[-10:]]).read_text(encoding="utf-8")
    return parse_product_page(html, card.url, NOW)


def test_shortlist_leaves_out_rejected_products_and_screens_for_the_kind(db):
    stock(db)
    everything = shortlist(db, FRIDGE, Needs(category="fridge"), None)
    assert len(everything.ranked) == 2 and "Titles were not screened" in everything.notes[0]
    first = everything.ranked[0]
    llm = FakeLLM([Keep(keep=[0])])
    short = shortlist(db, FRIDGE, Needs(category="fridge", kind="fridge_freezer"), llm, excluded={first.product_id})
    assert [c.product_id for c in short.ranked] == [everything.ranked[1].product_id]
    assert "The shopper wants: Fridge with a freezer part" in llm.requests[0][1]


def test_make_picks_rechecks_and_uses_the_needs_text(db):
    stock(db)
    needs = Needs(category="fridge", budget=9000)
    result = make_picks(shortlist(db, FRIDGE, needs, None), needs, 1, refetch, None)
    assert len(result.chosen) == 1 and result.conditions_text == "budget 9000 SEK"
    assert result.render().startswith("Conditions: budget 9000 SEK")


def test_cards_by_ids_keep_the_given_order(db):
    stock(db)
    ids = [c.listing_id for c in shortlist(db, FRIDGE, Needs(category="fridge"), None).ranked]
    assert [c.listing_id for c in load_cards_by_ids(db, ids[::-1])] == ids[::-1]
    assert load_cards_by_ids(db, [999]) == []
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `python -m pytest tests/test_picks_pipeline.py tests/test_picks_write.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scout.picks.pipeline'`, and the two new tests in `test_picks_write.py` fail.

- [ ] **Step 3: Share the claim checks**

In `src/scout/picks/write.py`, add these functions before `check_picks`. The follow-up answers in Task 7 use them too.

```python
def numbers_in(text: str) -> set[Decimal]:
    return {n for raw in SUMMARY_NUMBER.findall(text) if (n := to_decimal(raw)) is not None}


def check_claim(facts: dict[str, Decimal | str], field: str, value: str) -> tuple[str | None, Decimal | None]:
    """Return (error, number). The number is set when a numeric claim agrees with the card."""
    if field not in facts:
        return f"no field '{field}' on the card", None
    fact = facts[field]
    if isinstance(fact, str):
        return (None, None) if _as_text(value) == _as_text(fact) else (f"{field} is {fact}, not {value}", None)
    number = to_decimal(value)
    if number is None or number != fact:
        return f"{field} is {fact}, not {value}", None
    return None, number


def unchecked_numbers(text: str, allowed: set[Decimal], models: Sequence[str | None] = ()) -> list[str]:
    """Numbers in the text that are not allowed. Model numbers and the "/5" of a rating do not count."""
    text = RATING_SCALE.sub("", text)
    for model in models:
        if model:
            text = re.sub(re.escape(model), "", text, flags=re.IGNORECASE)
    return [raw for raw in SUMMARY_NUMBER.findall(text) if (n := to_decimal(raw)) is not None and n not in allowed]
```

Add `from typing import Sequence` to the imports. Then replace `check_picks` with:

```python
def check_picks(text: PicksText, cards: dict[int, Card], conditions_text: str = "") -> list[str]:
    errors = []
    given = numbers_in(conditions_text)
    for pick in text.picks:
        card = cards.get(pick.listing_id)
        if card is None:
            errors.append(f"unknown listing_id {pick.listing_id}")
            continue
        facts = card_facts(card)
        claimed: set[Decimal] = set()
        for claim in pick.claims:
            error, number = check_claim(facts, claim.field, claim.value)
            if error:
                errors.append(f"{pick.listing_id}: {error}")
            elif number is not None:
                claimed.add(number)
        for raw in unchecked_numbers(pick.summary, claimed | given, [card.model]):
            errors.append(f"{pick.listing_id}: the number {raw} in the summary is not in the claims. Add a claim "
                          f"with the card field that has it, or remove it. Numbers on this card: {number_facts(facts)}")
    missing_ids = sorted(set(cards) - {p.listing_id for p in text.picks})
    if missing_ids:
        errors.append(f"no pick written for ids {missing_ids}")
    return errors
```

The other error texts do not change, so the old tests in `tests/test_picks_write.py` must pass without changes.

Then add the feedback for the next round.

Create `src/scout/llm/feedback.py`:

```python
"""The prompt for the next round after Python found problems in the last answer."""
from pydantic import BaseModel


def retry_prompt(base: str, last: BaseModel | None, errors: list[str]) -> str:
    if last is None or not errors:
        return base
    return (f"{base}\n\nYour last answer:\n{last.model_dump_json()}\n\n"
            "Python compared it with the records and found these problems. Each problem gives the value "
            "in the record. Keep the parts that are correct, and fix only these:\n"
            + "\n".join(f"- {e}" for e in errors))
```

In `src/scout/picks/write.py`, add before `check_picks`:

```python
def number_facts(facts: dict[str, Decimal | str]) -> str:
    return ", ".join(f"{k}={v.normalize():f}" for k, v in facts.items() if isinstance(v, Decimal)) or "none"


def passed_picks(text: PicksText, errors: list[str]) -> PicksText | None:
    """Keep the picks with no problem. Each problem of one pick starts with "<listing_id>:"."""
    failed = {int(head) for e in errors if (head := e.split(":", 1)[0]).isdigit()}
    kept = [p for p in text.picks if p.listing_id not in failed]
    return PicksText(picks=kept) if kept else None
```

Replace `write_picks` with:

```python
def write_picks(llm, cards: list[Card], conditions_text: str, language: str = "English",
                max_rounds: int = 3) -> tuple[PicksText | None, list[str]]:
    """Return the checked texts and the problems that are left. A pick with a problem has no text."""
    by_id = {card.listing_id: card for card in cards}
    base = f"Conditions: {conditions_text or 'none'}\n\nCards:\n" + "\n".join(_card_json(c) for c in cards)
    text, errors = None, []
    for _ in range(max_rounds):
        text = llm.structured("chat", SYSTEM.format(language=language), retry_prompt(base, text, errors), PicksText)
        errors = check_picks(text, by_id, conditions_text)
        if not errors:
            return text, []
    return passed_picks(text, errors), errors
```

Import `retry_prompt` from `scout.llm.feedback`. In `format_picks`, replace the line `lines.append("The written summaries did not pass the checks, so only the facts are shown.")` with:

```python
        lines.append("The written summaries did not pass the checks, so only the facts are shown." if texts is None
                     else "Some summaries did not pass the checks. Those picks show only the facts.")
```

- [ ] **Step 4: Load cards by listing ID**

The conversation state keeps only listing IDs. Add to `src/scout/picks/rank.py`:

```python
CARDS_BY_ID_SQL = """
SELECT DISTINCT ON (l.id) l.id AS listing_id, p.id AS product_id, COALESCE(l.title, p.name) AS title, p.brand,
       p.model, l.url, v.price, v.currency, v.in_stock, l.rating, l.review_count, p.attributes, l.fetched_at
FROM listings l
JOIN products p ON p.id = l.product_id
JOIN variants v ON v.listing_id = l.id
WHERE l.id = ANY(%(ids)s)
ORDER BY l.id, v.updated_at DESC
"""


def load_cards_by_ids(conn: psycopg.Connection, listing_ids: list[int]) -> list[Card]:
    rows = conn.execute(CARDS_BY_ID_SQL, {"ids": list(listing_ids)}).fetchall()
    by_id = {row["listing_id"]: Card(**row) for row in rows}
    return [by_id[i] for i in listing_ids if i in by_id]
```

- [ ] **Step 5: Write the pipeline**

Create `src/scout/picks/pipeline.py`:

```python
"""The picks steps, shared by `scout picks` and the chat agent."""
from dataclasses import dataclass, field
from typing import Callable, Collection

import psycopg

from scout.crawl import OnProblem, fetch_checked
from scout.db.store import save_record
from scout.extract.screen import screen_titles
from scout.llm.client import LLMOutputError, LLMUnavailable
from scout.needs import Needs
from scout.picks.rank import Card, apply_conditions, drop_implausible_sizes, load_cards, rank
from scout.picks.recheck import recheck_picks
from scout.picks.write import PicksText, format_picks, write_picks
from scout.profiles import CategoryProfile
from scout.sources.base import ProductRecord

Refetch = Callable[[Card], ProductRecord | None]


@dataclass
class Shortlist:
    ranked: list[Card]
    dropped: int
    notes: list[str] = field(default_factory=list)
    size_notes: dict[int, str] = field(default_factory=dict)


@dataclass
class PicksResult:
    chosen: list[Card]
    texts: PicksText | None
    errors: list[str]
    notes: list[str]
    dropped: int
    conditions_text: str

    def render(self) -> str:
        return format_picks(self.chosen, self.texts, self.notes, self.conditions_text, self.dropped, self.errors)


def shortlist(conn: psycopg.Connection, profile: CategoryProfile, needs: Needs, llm,
              excluded: Collection[int] = ()) -> Shortlist:
    """SQL filter, implausible sizes, the user's conditions, the rank, and the title screening."""
    cards = [c for c in load_cards(conn, profile.category, needs.budget_sek()) if c.product_id not in excluded]
    size_notes = drop_implausible_sizes(cards)
    cards, dropped = apply_conditions(cards, needs.rank_conditions())
    ranked = rank(cards)
    notes: list[str] = []
    if llm is None:
        notes.append("Titles were not screened, so items outside the category can appear.")
    elif ranked:
        wanted = needs.wanted(profile)
        try:
            on_topic = screen_titles(ranked, profile, llm, wanted)
            if len(on_topic) < len(ranked):
                notes.append(f"{len(ranked) - len(on_topic)} stored items are not a "
                             f"'{wanted}' by their titles and were left out.")
            ranked = on_topic
        except (LLMUnavailable, LLMOutputError) as exc:
            notes.append(f"Title screening skipped: {exc}")
    return Shortlist(ranked, dropped, notes, size_notes)


def make_picks(short: Shortlist, needs: Needs, k: int, refetch: Refetch, llm,
               language: str = "English") -> PicksResult:
    """Recheck the top cards on the live pages, then write summaries with checked numbers."""
    chosen, recheck_notes = recheck_picks(short.ranked, k, refetch, needs.budget_sek(), needs.rank_conditions())
    notes = short.notes + recheck_notes + [short.size_notes[c.listing_id] for c in chosen
                                           if c.listing_id in short.size_notes]
    texts, errors = None, []
    if chosen and llm is not None:
        try:
            texts, errors = write_picks(llm, chosen, needs.text(), language)
        except LLMUnavailable as exc:
            notes.append(f"No summaries: {exc}")
    return PicksResult(chosen, texts, errors, notes, short.dropped, needs.text())


def refetcher(adapter, conn: psycopg.Connection, category: str, on_problem: OnProblem) -> Refetch:
    def refetch(card: Card) -> ProductRecord | None:
        page = fetch_checked(adapter, conn, card.url, on_problem)
        if page is None:
            return None
        rec = adapter.parse(page)
        save_record(conn, rec, category)
        return rec

    return refetch
```

- [ ] **Step 6: Make `scout picks` use the pipeline**

In `src/scout/cli.py`, replace the body of `picks` with:

```python
    settings = load_settings()
    profile = load_profile(category)
    conditions = [parse_condition(w) for w in where or []]
    needs = Needs(category=category, budget=budget,
                  conditions=[ConditionSpec(attribute=c.attribute, op=c.op, value=c.value) for c in conditions])
    for error in check_needs(needs, profile):
        raise typer.BadParameter(error)
    llm = None if no_llm else LLM(load_llm_config())
    with connect(settings.database_url) as conn:
        short = shortlist(conn, profile, needs, llm)
        if not short.ranked:
            typer.echo("No stored product meets these conditions. Run scout crawl first, or loosen them.")
            raise typer.Exit(1)
        with BrowserSession(settings.browser_profile_dir) as session:
            adapter = AmazonSeAdapter(session, settings.snapshot_dir)
            result = make_picks(short, needs, k, refetcher(adapter, conn, category, ask_user), llm, lang)
    typer.echo(result.render())
    if llm is not None:
        report_llm_use(llm)
```

Remove the imports that `cli.py` does not use now: `apply_conditions`, `drop_implausible_sizes`, `load_cards`, `rank`, `recheck_picks`, `format_picks`, `write_picks`, `fetch_checked`, `save_record`, `LLMOutputError` and `LLMUnavailable`. Keep `screen_titles`, because `crawl` uses it. Import `Needs`, `ConditionSpec` and `check_needs` from `scout.needs`, and `shortlist`, `make_picks` and `refetcher` from `scout.picks.pipeline`. Run `python -m pyflakes src/scout` to find imports that are not used.

- [ ] **Step 7: Run all tests and one real command**

Run: `python -m pytest -q`
Expected: all tests pass.

Run: `scout picks fridge --budget 9000 --where "height<=186" --where "width<=60" --no-llm`
Expected: the same 5 picks and facts as the `--no-llm` run in phase 1 Task 14, unless the prices changed. The command still opens the browser for the recheck.

- [ ] **Step 8: Commit**

```bash
git add src/scout/picks src/scout/cli.py tests/test_picks_pipeline.py
git commit -m "One picks pipeline for the command and the agent; shared claim checks; cards by listing ID"
```

---

### Task 4: Crawl a later result page, skip stored listings, record rejected products

**Files:**
- Modify: `src/scout/crawl.py`
- Modify: `src/scout/db/schema.sql`, `src/scout/db/store.py`
- Modify: `tests/conftest.py`, `tests/test_crawl.py`, `tests/test_store.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_crawl.py`:

```python
def test_a_later_crawl_can_start_at_page_2_and_skip_stored_listings(db, tmp_path):
    crawl(db, AmazonSeAdapter(site(), tmp_path), "fridge", "kylskåp", on_problem=never_called)
    browser = site()
    browser.pages[search_url("kylskåp", 2)] = [html("search_basic.html")]
    report = crawl(db, AmazonSeAdapter(browser, tmp_path), "fridge", "kylskåp", on_problem=never_called,
                   first_page=2, skip_known=True)
    assert browser.fetched == [search_url("kylskåp", 2)]
    assert (report.known, report.detail_pages) == (3, 0)


def test_stored_listings_get_the_rating_of_the_result_card(db, tmp_path):
    crawl(db, AmazonSeAdapter(site(), tmp_path), "fridge", "kylskåp", on_problem=never_called)
    db.execute("UPDATE listings SET rating = NULL, review_count = NULL")
    browser = site()
    browser.pages[search_url("kylskåp", 2)] = [html("search_basic.html")]
    crawl(db, AmazonSeAdapter(browser, tmp_path), "fridge", "kylskåp", on_problem=never_called,
          first_page=2, skip_known=True)
    row = db.execute("SELECT rating, review_count FROM listings WHERE source_id = 'B0AAAAAAA1'").fetchone()
    assert (row["rating"], row["review_count"]) == (Decimal("4.5"), 1234)
```

Add to `tests/test_store.py`:

```python
from scout.db.store import add_eliminations, eliminated_products, known_source_ids
from tests.test_picks_db import save


def test_rejected_products_belong_to_one_thread(db):
    save(db, "product_full.html", "B0AAAAAAA1")
    product_id = db.execute("SELECT id FROM products").fetchone()["id"]
    add_eliminations(db, "t1", [product_id], "too loud")
    assert eliminated_products(db, "t1") == {product_id}
    assert eliminated_products(db, "t2") == set()


def test_known_source_ids_finds_stored_listings(db):
    save(db, "product_full.html", "B0AAAAAAA1")
    assert known_source_ids(db, "amazon_se", ["B0AAAAAAA1", "B0AAAAAAA9"]) == {"B0AAAAAAA1"}
```

In `tests/conftest.py`, add `eliminations` to `TABLES`:

```python
TABLES = "eliminations, price_history, variants, listings, products, fetch_log"
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `python -m pytest tests/test_crawl.py tests/test_store.py -v`
Expected: FAIL. `crawl` has no `first_page` argument, and the table `eliminations` does not exist.

- [ ] **Step 3: Add the table and the store functions**

Add to `src/scout/db/schema.sql`:

```sql
CREATE TABLE IF NOT EXISTS eliminations (
    id          bigserial PRIMARY KEY,
    thread_id   text NOT NULL,
    product_id  bigint NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    reason      text NOT NULL DEFAULT '',
    removed_by  text NOT NULL CHECK (removed_by IN ('user', 'agent')),
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS eliminations_thread ON eliminations (thread_id);
```

Add to `src/scout/db/store.py`:

```python
def known_source_ids(conn: psycopg.Connection, source: str, source_ids: list[str]) -> set[str]:
    rows = conn.execute("SELECT source_id FROM listings WHERE source = %s AND source_id = ANY(%s)",
                        (source, source_ids)).fetchall()
    return {row["source_id"] for row in rows}


def refresh_listings(conn: psycopg.Connection, candidates: list[Candidate]) -> None:
    """Write the rating and the review count of fresh result cards to the stored listings."""
    for cand in candidates:
        if cand.rating is None and cand.review_count is None:
            continue
        conn.execute("UPDATE listings SET rating = COALESCE(%s, rating), review_count = COALESCE(%s, review_count) "
                     "WHERE source = %s AND source_id = %s",
                     (cand.rating, cand.review_count, cand.source, cand.source_id))
    conn.commit()


def add_eliminations(conn: psycopg.Connection, thread_id: str, product_ids: list[int], reason: str,
                     removed_by: str = "user") -> None:
    for product_id in product_ids:
        conn.execute("INSERT INTO eliminations (thread_id, product_id, reason, removed_by) VALUES (%s, %s, %s, %s)",
                     (thread_id, product_id, reason, removed_by))
    conn.commit()


def eliminated_products(conn: psycopg.Connection, thread_id: str) -> set[int]:
    rows = conn.execute("SELECT product_id FROM eliminations WHERE thread_id = %s", (thread_id,)).fetchall()
    return {row["product_id"] for row in rows}
```

Import `Candidate` from `scout.sources.base` in `store.py`, if it is not imported. Run `scout db-init` one time, so that the main database gets the new table.

- [ ] **Step 4: Change the crawl**

In `src/scout/crawl.py`:

1. Add `known: int = 0` to `CrawlReport`, after `off_topic`.
2. Add two keyword arguments to `crawl`: `first_page: int = 1` and `skip_known: bool = False`.
3. Change the search loop to `for number in range(first_page, first_page + max_pages):`.
4. After the title screening and before the detail pages, add:

```python
    if skip_known and candidates:
        known = known_source_ids(conn, candidates[0].source, [c.source_id for c in candidates])
        report.known = len(known)
        refresh_listings(conn, [c for c in candidates if c.source_id in known])
        candidates = [c for c in candidates if c.source_id not in known]
```

5. Import `known_source_ids` and `refresh_listings` from `scout.db.store`.

The agent uses `skip_known=True`, because the recheck before the picks fetches the stored products again anyway. The result card refreshes the rating and the review count of a stored product. The card price is not written, because a card can show the price of another variant. The recheck reads the price of each pick. `scout crawl` keeps the old behaviour.

- [ ] **Step 5: Run all tests**

Run: `python -m pytest -q`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/scout/crawl.py src/scout/db tests/conftest.py tests/test_crawl.py tests/test_store.py
git commit -m "Crawl from a given result page and skip stored listings; eliminations table for rejected products"
```

---

### Task 5: What the model sees of the conversation

The state keeps all messages. The chat model sees a summary of the old turns and the recent messages in full. This keeps the prompts small on the free tiers (Groq allows 8,000 tokens each minute).

**Files:**
- Create: `src/scout/agent/__init__.py`, `src/scout/agent/history.py`
- Create: `tests/test_history.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_history.py`:

```python
from decimal import Decimal

from scout.agent.history import (COMPACT_AFTER, KEEP, Summary, assistant, compact, history_text, last_user_text,
                                 user_numbers)
from tests.fakes import FakeLLM


def user(text):
    return {"role": "user", "content": text}


def test_history_shows_the_summary_and_the_messages_after_it():
    state = {"summary": "Wants a fridge.", "summarized": 1,
             "messages": [user("old"), user("höjd 186"), assistant("x" * 3000)]}
    text = history_text(state)
    assert text.startswith("Summary of the earlier conversation: Wants a fridge.")
    assert "old" not in text and "Shopper: höjd 186" in text
    assert text.endswith("[...]") and len(text) < 2200


def test_user_numbers_and_last_user_text():
    state = {"messages": [user("bredd 60, höjd 1,8 m"), assistant("Budget 9000?"), user("9 000 kr")]}
    assert user_numbers(state) == {Decimal("60"), Decimal("1.8"), Decimal("9000")}
    assert last_user_text(state) == "9 000 kr"


def test_compact_waits_until_the_history_is_long():
    llm = FakeLLM([Summary(summary="Wants a quiet fridge.")])
    short = {"messages": [user(str(n)) for n in range(COMPACT_AFTER)]}
    assert compact(short, llm) == {} and llm.requests == []
    long = {"messages": [user(str(n)) for n in range(COMPACT_AFTER + 1)]}
    assert compact(long, llm) == {"summary": "Wants a quiet fridge.", "summarized": COMPACT_AFTER + 1 - KEEP}
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `python -m pytest tests/test_history.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scout.agent'`.

- [ ] **Step 3: Write the module**

Create `src/scout/agent/__init__.py`:

```python
"""The conversation agent: a LangGraph graph over the phase 1 steps."""
```

Create `src/scout/agent/history.py`:

```python
"""What the chat model sees of the conversation: a summary of the old turns and the recent messages."""
from decimal import Decimal

from pydantic import BaseModel

from scout.picks.write import numbers_in

KEEP = 12            # messages that stay in full after a summary
COMPACT_AFTER = 20   # messages after the last summary that start a new summary
MAX_CHARS = 2000     # a long message, such as the picks, is cut in the prompt

SUMMARY_SYSTEM = """You summarize a shopping conversation for a later step of the same assistant.
Keep the needs of the shopper, the products that they rejected and why, and their open questions.
Do not add facts. Write at most 120 words, in English."""


class Summary(BaseModel):
    summary: str


def assistant(text: str) -> dict:
    return {"role": "assistant", "content": text}


def history_text(state: dict) -> str:
    lines = []
    if state.get("summary"):
        lines.append(f"Summary of the earlier conversation: {state['summary']}")
    for message in state.get("messages", [])[state.get("summarized", 0):]:
        who = "Shopper" if message["role"] == "user" else "Assistant"
        text = message["content"]
        if len(text) > MAX_CHARS:
            text = text[:MAX_CHARS] + " [...]"
        lines.append(f"{who}: {text}")
    return "\n".join(lines)


def last_user_text(state: dict) -> str:
    return next((m["content"] for m in reversed(state.get("messages", [])) if m["role"] == "user"), "")


def user_numbers(state: dict) -> set[Decimal]:
    """All numbers that the shopper wrote in this thread. Checks of later steps allow them."""
    numbers: set[Decimal] = set()
    for message in state.get("messages", []):
        if message["role"] == "user":
            numbers |= numbers_in(message["content"])
    return numbers


def compact(state: dict, llm) -> dict:
    messages, done = state.get("messages", []), state.get("summarized", 0)
    if len(messages) - done <= COMPACT_AFTER:
        return {}
    upto = len(messages) - KEEP
    old = history_text({"summary": state.get("summary", ""), "messages": messages[:upto], "summarized": done})
    reply = llm.structured("chat", SUMMARY_SYSTEM, old, Summary)
    return {"summary": reply.summary, "summarized": upto}
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_history.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/scout/agent tests/test_history.py
git commit -m "Conversation history for the chat model: summary of old turns, recent messages, the user's numbers"
```

---

### Task 6: Clarify the needs

**Files:**
- Create: `src/scout/agent/clarify.py`
- Create: `tests/test_clarify.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_clarify.py`:

```python
from decimal import Decimal

import pytest

from scout.agent.clarify import CategoryChoice, ClarifyReply, clarify, pick_category
from scout.llm.client import LLMOutputError
from scout.needs import ConditionSpec, Needs
from scout.profiles import load_profile
from tests.fakes import FakeLLM

FRIDGE = load_profile("fridge")


def reply(ready, question="", kind=None, conditions=(), budget=None):
    return ClarifyReply(needs=Needs(kind=kind, conditions=list(conditions), budget=budget), ready=ready,
                        question=question, language="Chinese")


def test_a_question_comes_back_while_the_kind_is_open():
    llm = FakeLLM([reply(False, "哪种冰箱？")])
    out = clarify(llm, FRIDGE, Needs(category="fridge"), "Shopper: 我想买冰箱", set())
    assert (out.ready, out.question, out.needs.category) == (False, "哪种冰箱？", "fridge")
    assert "- fridge_freezer: Fridge with a freezer part" in llm.requests[0][1]


def test_an_invented_number_is_sent_back_and_fixed():
    height = ConditionSpec(attribute="height", op="<=", value=185)
    llm = FakeLLM([reply(True, kind="fridge_freezer", conditions=[height]),
                   reply(True, kind="fridge_freezer", conditions=[height.model_copy(update={"value": 186})])])
    out = clarify(llm, FRIDGE, Needs(category="fridge"), "Shopper: höjd 186", {Decimal("186")})
    assert out.needs.conditions[0].value == 186
    assert "height 185 is not a number the shopper wrote" in llm.requests[1][1]


def test_ready_without_a_kind_is_not_accepted():
    with pytest.raises(LLMOutputError):
        clarify(FakeLLM([reply(True)] * 3), FRIDGE, Needs(category="fridge"), "Shopper: search now", set())


def test_pick_category_accepts_only_known_names():
    assert pick_category(FakeLLM([CategoryChoice(category="fridge")]), "Shopper: kylskåp", ["fridge"]) == "fridge"
    assert pick_category(FakeLLM([CategoryChoice(category="sofa")]), "Shopper: soffa", ["fridge"]) is None
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `python -m pytest tests/test_clarify.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write the clarify step**

Create `src/scout/agent/clarify.py`:

```python
"""Clarify the needs: compare what the shopper said with the category profile, and ask until the needs are clear."""
from decimal import Decimal

from pydantic import BaseModel

from scout.llm.client import LLMOutputError
from scout.llm.feedback import retry_prompt
from scout.needs import Needs, check_needs
from scout.profiles import CategoryProfile

SYSTEM = """You help a shopper say what they want, before a search on amazon.se.
You get the category profile, the needs so far and the conversation. Return the updated needs.
Rules:
- Use only what the shopper said. Never guess a size, a budget or a kind.
- kind is the name of one kind in the profile, or null.
- conditions use only number attributes of the profile, in the unit of the profile.
  Change units when necessary, for example 1,8 m is 180 cm. "At most" is <=, "at least" is >=.
- budget is in SEK, or null. If the shopper gives a budget in another currency, ask for SEK.
- preferences are short notes of soft wishes, for example "quiet" or "white".
- search_terms_sv is one to four Swedish words for the amazon.se search for this kind, or null.
- ready is true when the kind is known and the shopper answered or skipped the questions of the profile.
  When the shopper asks you to search now, ready is true if the kind is known.
- When ready is false, write one short message in question. It can ask about two or three things.
  Write it in the shopper's language.
- language is the language that the shopper writes in, as an English word, for example Chinese."""

CATEGORY_SYSTEM = """You find which product category a shopper wants.
Return the name of one category from the list, or null when none fits or the shopper did not say."""


class ClarifyReply(BaseModel):
    needs: Needs
    ready: bool
    question: str = ""
    language: str = "English"


class CategoryChoice(BaseModel):
    category: str | None = None


def pick_category(llm, history: str, names: list[str]) -> str | None:
    prompt = f"Categories: {', '.join(names)}\n\nConversation:\n{history}"
    choice = llm.structured("chat", CATEGORY_SYSTEM, prompt, CategoryChoice)
    return choice.category if choice.category in names else None


def describe_profile(profile: CategoryProfile) -> str:
    lines = [f"Category: {profile.display_name}", "Kinds:"]
    lines += [f"- {k.name}: {k.label}" for k in profile.kinds]
    lines.append("Attributes:")
    for attr in profile.attributes:
        detail = attr.unit or (", ".join(attr.values) if attr.values else attr.type)
        lines.append(f"- {attr.name} ({attr.type}, {detail}, {attr.kind})")
    lines.append("Questions to cover:")
    lines += [f"- {q}" for q in profile.questions_for_user]
    return "\n".join(lines)


def clarify(llm, profile: CategoryProfile, needs: Needs, history: str, said: set[Decimal],
            max_rounds: int = 3) -> ClarifyReply:
    base = f"{describe_profile(profile)}\n\nNeeds so far: {needs.model_dump_json()}\n\nConversation:\n{history}"
    reply, errors = None, []
    for _ in range(max_rounds):
        reply = llm.structured("chat", SYSTEM, retry_prompt(base, reply, errors), ClarifyReply)
        reply.needs.category = profile.category
        errors = check_needs(reply.needs, profile, said)
        if reply.ready and profile.kinds and reply.needs.kind is None:
            errors.append("ready is true, but the kind is not known")
        if not reply.ready and not reply.question.strip():
            errors.append("ready is false, so question must not be empty")
        if not errors:
            return reply
    raise LLMOutputError(f"The needs were still not valid after {max_rounds} tries: {errors}")
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_clarify.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/scout/agent/clarify.py tests/test_clarify.py
git commit -m "Clarify step: needs from the conversation, checked against the profile and the user's numbers"
```

---

### Task 7: Follow-up routing and checked answers

**Files:**
- Create: `src/scout/agent/followup.py`
- Create: `tests/test_followup.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_followup.py`:

```python
from decimal import Decimal

from scout.agent.followup import Answer, AnswerClaim, Route, answer_question, check_answer, choose_route
from tests.fakes import FakeLLM
from tests.test_picks_rank import card

PICKS = [{"listing_id": 1, "product_id": 1, "title": "Bomann KG 320"},
         {"listing_id": 2, "product_id": 2, "title": "FOHERE 90L"}]
CARDS = [card(1, 4.4, 1779, height=143.0, no_frost=True), card(2, 4.2, 54, height=48.0)]


def test_route_drops_pick_numbers_that_do_not_exist():
    llm = FakeLLM([Route(action="exclude", pick_numbers=[2, 2, 7], reason="too expensive")])
    route = choose_route(llm, PICKS, "Shopper: remove 2 and 7")
    assert route.pick_numbers == [2]
    assert "1. Bomann KG 320" in llm.requests[0][1]


def test_check_answer_allows_claims_the_shoppers_numbers_and_pick_numbers():
    good = Answer(text="Pick 1 is 143 cm high, under your 186.",
                  claims=[AnswerClaim(pick=1, field="height", value="143")])
    assert check_answer(good, CARDS, {Decimal("186")}) == []
    bad = Answer(text="Pick 2 is 80 cm high.", claims=[AnswerClaim(pick=2, field="height", value="80")])
    errors = check_answer(bad, CARDS, set())
    assert any("height is 48.0, not 80" in e for e in errors)
    assert any("the number 80" in e for e in errors)
    no_pick = Answer(text="ok", claims=[AnswerClaim(pick=3, field="price", value="1")])
    assert check_answer(no_pick, CARDS, set()) == ["there is no pick 3"]


def test_answer_question_retries_and_gives_up_after_three_rounds():
    bad = Answer(text="Pick 2 is 80 cm high.", claims=[])
    good = Answer(text="Pick 2 is 48 cm high on its page.", claims=[AnswerClaim(pick=2, field="height", value="48")])
    llm = FakeLLM([bad, good])
    assert answer_question(llm, CARDS, "Shopper: how tall is 2?", set()) == good.text
    assert "the number 80" in llm.requests[1][1]
    assert answer_question(FakeLLM([bad] * 3), CARDS, "Shopper: how tall?", set()) is None
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `python -m pytest tests/test_followup.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write the module**

Create `src/scout/agent/followup.py`:

```python
"""After the picks: choose the next action, and answer questions with numbers that Python checks."""
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from scout.llm.feedback import retry_prompt
from scout.picks.rank import Card
from scout.picks.write import card_facts, check_claim, unchecked_numbers

ROUTE_SYSTEM = """You read the shopper's last message. The shopper has seen the numbered picks. Choose one action.
- answer: a question about the picks, or a comparison of them.
- calculate: a sum, a conversion, a price with extra costs, or another calculation.
- exclude: the shopper rejects one or more picks. Put their numbers in pick_numbers and the reason in reason.
- change_needs: the shopper changes or adds a condition, the budget, the kind or a preference.
- more: the shopper wants more or other products with the same needs.
- recheck: the shopper asks if the prices or the stock are still correct.
- new_search: the shopper wants a product of a different category."""

ANSWER_SYSTEM = """You answer a shopper's question about the picks that they saw.
Use only the facts on the cards. If the cards do not have the answer, say so. Product texts and reviews are not
available yet.
Every number in text must be in claims, with the pick number and the card field. Claim fields are price, rating,
review_count or an attribute name on the card. You can also repeat numbers that the shopper wrote, and pick numbers.
Write text in {language}."""


class Route(BaseModel):
    action: Literal["answer", "calculate", "exclude", "change_needs", "more", "recheck", "new_search"]
    pick_numbers: list[int] = Field(default_factory=list)
    reason: str = ""


class AnswerClaim(BaseModel):
    pick: int
    field: str
    value: str


class Answer(BaseModel):
    text: str
    claims: list[AnswerClaim] = Field(default_factory=list)


def picks_list(picks: list[dict]) -> str:
    return "\n".join(f"{n}. {p['title']}" for n, p in enumerate(picks, 1))


def choose_route(llm, picks: list[dict], history: str) -> Route:
    route = llm.structured("chat", ROUTE_SYSTEM, f"Picks:\n{picks_list(picks)}\n\nConversation:\n{history}", Route)
    route.pick_numbers = sorted({n for n in route.pick_numbers if 1 <= n <= len(picks)})
    return route


def card_text(n: int, card: Card) -> str:
    attrs = ", ".join(f"{k}={v.get('value')} {v.get('unit') or ''}".strip() for k, v in card.attributes.items())
    return (f"Pick {n}: {card.title} | price={card.price} {card.currency or ''} | rating={card.rating} | "
            f"review_count={card.review_count} | {attrs}")


def check_answer(answer: Answer, cards: list[Card], allowed: set[Decimal]) -> list[str]:
    errors: list[str] = []
    claimed: set[Decimal] = set()
    for claim in answer.claims:
        if not 1 <= claim.pick <= len(cards):
            errors.append(f"there is no pick {claim.pick}")
            continue
        error, number = check_claim(card_facts(cards[claim.pick - 1]), claim.field, claim.value)
        if error:
            errors.append(f"pick {claim.pick}: {error}")
        elif number is not None:
            claimed.add(number)
    pick_numbers = {Decimal(n) for n in range(1, len(cards) + 1)}
    for raw in unchecked_numbers(answer.text, claimed | allowed | pick_numbers, [c.model for c in cards]):
        errors.append(f"the number {raw} in text is not in the claims. Add a claim with the pick and the field "
                      "that has it, or remove it")
    return errors


def answer_question(llm, cards: list[Card], history: str, allowed: set[Decimal], language: str = "English",
                    max_rounds: int = 3) -> str | None:
    """Return the checked answer, or None when no round passed the checks."""
    base = "Cards:\n" + "\n".join(card_text(n, c) for n, c in enumerate(cards, 1)) + f"\n\nConversation:\n{history}"
    answer, errors = None, []
    for _ in range(max_rounds):
        answer = llm.structured("chat", ANSWER_SYSTEM.format(language=language),
                                retry_prompt(base, answer, errors), Answer)
        errors = check_answer(answer, cards, allowed)
        if not errors:
            return answer.text
    return None
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_followup.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/scout/agent/followup.py tests/test_followup.py
git commit -m "Follow-up routing as structured output; answers from the cards with checked numbers"
```

---

### Task 8: The calculation tool

The chat model writes the steps. Python does the arithmetic and shows the formula. Each number in a step must be a fact on a pick, a number that the user wrote, an earlier step, or the constant 100 for percentages.

**Files:**
- Create: `src/scout/agent/calc.py`
- Create: `tests/test_calc.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_calc.py`:

```python
from decimal import Decimal

import pytest

from scout.agent.calc import CalcError, CalcPlan, Operand, Step, calculate, run_plan
from tests.fakes import FakeLLM
from tests.test_picks_rank import card

CARDS = [card(1)]  # price 5000 SEK
PRICE = Operand(ref="pick 1 price")


def plan(*steps, result="total", explanation="Price with shipping."):
    return CalcPlan(steps=[Step(name=n, op=op, a=a, b=b) for n, op, a, b in steps], result=result,
                    explanation=explanation)


def test_price_plus_shipping_shows_the_formula():
    value, lines = run_plan(plan(("total", "add", PRICE, Operand(number="499"))), CARDS, {Decimal("499")})
    assert value == Decimal("5499")
    assert lines == ["total = 5000 (pick 1 price) + 499 = 5499"]


def test_a_percentage_uses_earlier_steps():
    steps = plan(("vat", "multiply", PRICE, Operand(number="25")),
                 ("vat_part", "divide", Operand(ref="vat"), Operand(number="100")),
                 ("total", "add", PRICE, Operand(ref="vat_part")))
    value, lines = run_plan(steps, CARDS, {Decimal("25"), Decimal("100")})
    assert value == Decimal("6250") and lines[1] == "vat_part = vat / 100 = 1250"


def test_a_number_the_shopper_did_not_write_is_refused():
    with pytest.raises(CalcError, match="11.5 is not a number that you wrote"):
        run_plan(plan(("total", "multiply", PRICE, Operand(number="11.5"))), CARDS, set())


def test_unknown_refs_and_division_by_zero_are_refused():
    with pytest.raises(CalcError, match="'pick 4 price'"):
        run_plan(plan(("total", "add", Operand(ref="pick 4 price"), Operand(number="1"))), CARDS, {Decimal("1")})
    with pytest.raises(CalcError, match="division by zero"):
        run_plan(plan(("total", "divide", PRICE, Operand(number="0"))), CARDS, {Decimal("0")})


def test_calculate_asks_for_a_missing_rate_and_shows_the_python_result():
    ask = CalcPlan(steps=[], result="", explanation="Which exchange rate do you want to use?")
    assert calculate(FakeLLM([ask]), CARDS, "Shopper: the price in euro?", set()) == ask.explanation
    text = calculate(FakeLLM([plan(("total", "add", PRICE, Operand(number="499")))]), CARDS,
                     "Shopper: plus 499 for shipping", {Decimal("499")})
    assert text.splitlines()[:2] == ["Price with shipping.", "total = 5000 (pick 1 price) + 499 = 5499"]
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `python -m pytest tests/test_calc.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write the tool**

Create `src/scout/agent/calc.py`:

```python
"""The calculation tool. The chat model writes the steps. Python does the arithmetic and shows the formula."""
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from pydantic import BaseModel, Field

from scout.agent.followup import card_text
from scout.picks.rank import Card
from scout.picks.write import card_facts, unchecked_numbers
from scout.sources.amazon_se.numbers import to_decimal

SYSTEM = """You change a shopper's calculation request into steps. You never calculate yourself.
Each step has a name, an operation (add, subtract, multiply or divide) and two operands, a and b.
An operand is one of these:
- a number that the shopper wrote, copied into number
- a fact of a pick, written into ref as "pick N field", for example "pick 1 price"
- the name of an earlier step, written into ref
For a percentage, divide by 100 in a step. result is the name of the step with the answer.
explanation is one short sentence in {language} that says what the result is. It has no numbers.
If the request needs a number that the shopper did not give, such as an exchange rate,
return no steps and ask for that number in explanation."""

CONSTANTS = {Decimal("100")}  # for percentages
SIGNS = {"add": "+", "subtract": "-", "multiply": "*", "divide": "/"}


class CalcError(ValueError):
    pass


class Operand(BaseModel):
    number: str | None = None
    ref: str | None = None


class Step(BaseModel):
    name: str
    op: Literal["add", "subtract", "multiply", "divide"]
    a: Operand
    b: Operand


class CalcPlan(BaseModel):
    steps: list[Step] = Field(default_factory=list)
    result: str = ""
    explanation: str


def fmt(number: Decimal) -> str:
    return format(number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP).normalize(), "f")


def run_plan(plan: CalcPlan, cards: list[Card], said: set[Decimal]) -> tuple[Decimal, list[str]]:
    values: dict[str, Decimal] = {}
    lines: list[str] = []

    def operand(op: Operand) -> tuple[Decimal, str]:
        if op.number is not None:
            number = to_decimal(op.number)
            if number is None or number not in said:
                raise CalcError(f"{op.number} is not a number that you wrote")
            return number, fmt(number)
        ref = (op.ref or "").strip()
        if ref in values:
            return values[ref], ref
        parts = ref.split()
        if len(parts) == 3 and parts[0] == "pick" and parts[1].isdigit() and 1 <= int(parts[1]) <= len(cards):
            fact = card_facts(cards[int(parts[1]) - 1]).get(parts[2])
            if isinstance(fact, Decimal):
                return fact, f"{fmt(fact)} ({ref})"
        raise CalcError(f"'{ref}' is not a number on a pick or an earlier step")

    for step in plan.steps:
        (a, a_text), (b, b_text) = operand(step.a), operand(step.b)
        if step.op == "add":
            value = a + b
        elif step.op == "subtract":
            value = a - b
        elif step.op == "multiply":
            value = a * b
        elif b == 0:
            raise CalcError("division by zero")
        else:
            value = a / b
        values[step.name] = value
        lines.append(f"{step.name} = {a_text} {SIGNS[step.op]} {b_text} = {fmt(value)}")
    if plan.result not in values:
        raise CalcError(f"the result '{plan.result}' is not one of the steps")
    return values[plan.result], lines


def calculate(llm, cards: list[Card], history: str, said: set[Decimal], language: str = "English") -> str:
    prompt = "Cards:\n" + "\n".join(card_text(n, c) for n, c in enumerate(cards, 1)) + f"\n\nConversation:\n{history}"
    plan = llm.structured("chat", SYSTEM.format(language=language), prompt, CalcPlan)
    if not plan.steps:
        return plan.explanation
    try:
        _, lines = run_plan(plan, cards, said | CONSTANTS)
    except CalcError as exc:
        return f"I could not calculate this: {exc}."
    allowed = said | {Decimal(n) for n in range(1, len(cards) + 1)}
    explanation = plan.explanation if not unchecked_numbers(plan.explanation, allowed) else "Result:"
    return "\n".join([explanation, *lines, "Python did the arithmetic. Results are rounded to 2 decimals."])
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_calc.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/scout/agent/calc.py tests/test_calc.py
git commit -m "Calculation tool: the model writes steps, Python calculates and shows the formula"
```

---

### Task 9: The conversation graph

**Files:**
- Create: `src/scout/agent/state.py`, `src/scout/agent/graph.py`
- Create: `tests/test_graph.py`

The graph:

```
START → compact → phase is picks? ── no ──→ clarify ── ready ──→ search → recommend → END
                        │                     └─ not ready: question → END
                        └── yes ──→ route ─┬─ answer ──────────→ END
                                           ├─ calculate ───────→ END
                                           ├─ exclude ─────────→ recommend → END
                                           ├─ change_needs ────→ clarify
                                           ├─ more ────────────→ search (always crawls)
                                           ├─ recheck ─────────→ recommend
                                           └─ new_search ──────→ reset → clarify
```

Each node except `compact` catches `LLMUnavailable` and `LLMOutputError`. The node then gives a short message and the turn ends. The state stays as it was, so the user can try again.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_graph.py`:

```python
from langgraph.checkpoint.memory import InMemorySaver

from scout.agent.clarify import CategoryChoice, ClarifyReply
from scout.agent.followup import Answer, AnswerClaim, Route
from scout.agent.graph import Deps, build_graph
from scout.crawl import CrawlReport
from scout.llm.client import QuotaExhausted
from scout.needs import Needs
from scout.picks.pipeline import PicksResult
from scout.profiles import load_profile
from tests.fakes import FakeLLM
from tests.test_picks_rank import card

FRIDGE = load_profile("fridge")


class World:
    """A fake shop and database behind the graph."""

    def __init__(self, stored=20):
        self.stored, self.crawls, self.removed, self.said = stored, [], {}, []
        self.cards = {1: card(1, 4.5, 100, height=143.0), 2: card(2, 4.0, 50, height=170.0)}

    def deps(self, llm):
        return Deps(
            llm=llm,
            profile_names=lambda: ["fridge"],
            load_profile=lambda name: FRIDGE,
            count=lambda profile, needs, excluded: self.stored,
            crawl=self.crawl,
            picks=self.picks,
            cards=lambda ids: [self.cards[i] for i in ids],
            eliminate=lambda thread, ids, reason: self.removed.setdefault(thread, set()).update(ids),
            eliminated=lambda thread: self.removed.get(thread, set()),
            say=self.said.append,
        )

    def crawl(self, profile, needs, page):
        self.crawls.append((needs.search_terms(profile), page))
        return CrawlReport(candidates=48, saved=20)

    def picks(self, profile, needs, excluded, language):
        chosen = [c for c in self.cards.values() if c.product_id not in excluded]
        return PicksResult(chosen, None, [], [], 0, needs.text())


def run(graph, thread, text):
    return graph.invoke({"messages": [{"role": "user", "content": text}]}, {"configurable": {"thread_id": thread}})


def ready(**fields):
    return ClarifyReply(needs=Needs(kind="fridge_freezer", **fields), ready=True, language="English")


def test_clarify_asks_first_and_does_not_search():
    world = World()
    llm = FakeLLM([CategoryChoice(category="fridge"), ClarifyReply(needs=Needs(), ready=False, question="Which kind?")])
    state = run(build_graph(world.deps(llm), InMemorySaver()), "t1", "I want a fridge")
    assert state["messages"][-1]["content"] == "Which kind?"
    assert state["phase"] == "clarify" and world.crawls == []


def test_a_new_thread_reads_page_1_once_then_uses_the_stored_products():
    world = World(stored=20)
    llm = FakeLLM([CategoryChoice(category="fridge"), ready(budget=9000), Route(action="change_needs"),
                   ready(budget=8000)])
    graph = build_graph(world.deps(llm), InMemorySaver())
    state = run(graph, "t1", "A fridge with a freezer, budget 9000")
    assert world.crawls == [("kyl och frys", 1)] and state["pages"] == {"kyl och frys": 1}
    assert state["phase"] == "picks" and [p["listing_id"] for p in state["picks"]] == [1, 2]
    assert state["messages"][-1]["content"].startswith("Conditions: budget 9000 SEK")
    state = run(graph, "t1", "Make the budget 8000")
    assert world.crawls == [("kyl och frys", 1)]
    assert "enough stored products match" in state["messages"][-2]["content"]


def test_few_stored_products_start_a_search_with_the_kind_terms():
    world = World(stored=3)
    llm = FakeLLM([CategoryChoice(category="fridge"), ready()])
    state = run(build_graph(world.deps(llm), InMemorySaver()), "t1", "A fridge with a freezer")
    assert world.crawls == [("kyl och frys", 1)] and state["pages"] == {"kyl och frys": 1}
    assert "Searched amazon.se for 'kyl och frys'" in state["messages"][-2]["content"]
    assert world.said and "kyl och frys" in world.said[0]


def test_follow_ups_in_one_thread():
    world = World()
    llm = FakeLLM([CategoryChoice(category="fridge"), ready(),
                   Route(action="exclude", pick_numbers=[1], reason="too small"),
                   Route(action="answer"),
                   Answer(text="Pick 1 is 170 cm high.", claims=[AnswerClaim(pick=1, field="height", value="170")]),
                   Route(action="more")])
    graph = build_graph(world.deps(llm), InMemorySaver())
    run(graph, "t1", "A fridge with a freezer")
    state = run(graph, "t1", "Remove the first one, it is too small")
    assert world.removed["t1"] == {1} and [p["listing_id"] for p in state["picks"]] == [2]
    state = run(graph, "t1", "How tall is it?")
    assert state["messages"][-1]["content"] == "Pick 1 is 170 cm high."
    state = run(graph, "t1", "Show me more")
    assert world.crawls == [("kyl och frys", 1), ("kyl och frys", 2)] and state["phase"] == "picks"


def test_exclude_without_a_valid_number_asks_which_pick():
    world = World()
    llm = FakeLLM([CategoryChoice(category="fridge"), ready(), Route(action="exclude", pick_numbers=[7])])
    graph = build_graph(world.deps(llm), InMemorySaver())
    run(graph, "t1", "A fridge with a freezer")
    state = run(graph, "t1", "Remove that one")
    assert "Which pick" in state["messages"][-1]["content"] and world.removed == {}


def test_an_unavailable_model_gives_a_message_and_the_turn_ends():
    world = World()
    llm = FakeLLM([QuotaExhausted("The free daily quota is used up.")])
    state = run(build_graph(world.deps(llm), InMemorySaver()), "t1", "I want a fridge")
    assert "quota is used up" in state["messages"][-1]["content"]
    assert state.get("phase") is None and world.crawls == []
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `python -m pytest tests/test_graph.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scout.agent.graph'`.

- [ ] **Step 3: Write the state**

Create `src/scout/agent/state.py`:

```python
"""The state of one conversation thread. It keeps product IDs, never full product records."""
import operator
from typing import Annotated, TypedDict


class ChatState(TypedDict, total=False):
    messages: Annotated[list[dict], operator.add]
    summary: str
    summarized: int    # messages before this index are in the summary
    phase: str         # clarify, search, recommend or picks
    needs: dict        # Needs as JSON
    language: str
    picks: list[dict]  # listing_id, product_id and title, in the order shown
    pages: dict        # search terms -> the last amazon.se result page read in this thread
    route: dict        # the last follow-up decision
```

- [ ] **Step 4: Write the graph**

Create `src/scout/agent/graph.py`:

```python
"""The conversation graph: clarify, search, recommend, and the follow-up actions."""
from dataclasses import dataclass, field
from typing import Any, Callable

from langgraph.graph import END, START, StateGraph

from scout.agent.calc import calculate
from scout.agent.clarify import clarify, pick_category
from scout.agent.followup import answer_question, choose_route
from scout.agent.history import assistant, compact, history_text, user_numbers
from scout.agent.state import ChatState
from scout.crawl import CrawlReport
from scout.llm.client import LLMOutputError, LLMUnavailable
from scout.needs import Needs
from scout.picks.pipeline import PicksResult
from scout.picks.rank import Card
from scout.picks.write import numbers_in
from scout.profiles import CategoryProfile
from scout.trace import NoTracer

MIN_STORED = 10  # after the first search of a thread, fewer stored matches than this start a new search

AFTER_ROUTE = {"answer": "answer", "calculate": "calculate", "exclude": "exclude", "change_needs": "clarify",
               "more": "search", "recheck": "recommend", "new_search": "reset", "stop": END}


@dataclass
class Deps:
    llm: Any
    profile_names: Callable[[], list[str]]
    load_profile: Callable[[str], CategoryProfile]
    count: Callable[[CategoryProfile, Needs, set[int]], int]
    crawl: Callable[[CategoryProfile, Needs, int], CrawlReport]
    picks: Callable[[CategoryProfile, Needs, set[int], str], PicksResult]
    cards: Callable[[list[int]], list[Card]]
    eliminate: Callable[[str, list[int], str], None]
    eliminated: Callable[[str], set[int]]
    say: Callable[[str], None] = print
    tracer: Any = field(default_factory=NoTracer)


def crawl_text(report: CrawlReport, terms: str, page: int) -> str:
    line = (f"Searched amazon.se for '{terms}' (result page {page}): {report.candidates} results, "
            f"{report.off_topic} left out by title, {report.over_budget} over budget, "
            f"{report.known} already stored (rating and review count updated), {report.saved} new products saved.")
    return "\n".join([line, *report.notes])


def thread_of(config) -> str:
    return config["configurable"]["thread_id"]


def build_graph(deps: Deps, checkpointer=None):
    llm = deps.llm

    def guarded(name, fn):
        def node(state, config):
            with deps.tracer.span(name):
                try:
                    return fn(state, config)
                except (LLMUnavailable, LLMOutputError) as exc:
                    return {"messages": [assistant(f"I cannot finish this step now. {exc}")],
                            "route": {"action": "stop"}}
        return node

    def compact_node(state, config):
        with deps.tracer.span("compact"):
            try:
                return compact(state, llm)
            except (LLMUnavailable, LLMOutputError):
                return {}  # the model then sees all messages since the last summary

    def needs_of(state) -> Needs:
        return Needs.model_validate(state.get("needs") or {})

    def clarify_node(state, config):
        needs, history = needs_of(state), history_text(state)
        if needs.category is None:
            names = deps.profile_names()
            category = pick_category(llm, history, names)
            if category is None:
                return {"phase": "clarify", "messages": [assistant(
                    f"I can search these categories now: {', '.join(names)}. Which one do you want?")]}
            needs.category = category
        reply = clarify(llm, deps.load_profile(needs.category), needs, history, user_numbers(state))
        update = {"needs": reply.needs.model_dump(mode="json"), "language": reply.language}
        if reply.ready:
            return {**update, "phase": "search"}
        return {**update, "phase": "clarify", "messages": [assistant(reply.question)]}

    def search_node(state, config):
        needs = needs_of(state)
        profile = deps.load_profile(needs.category)
        more = (state.get("route") or {}).get("action") == "more"
        terms = needs.search_terms(profile)
        pages = dict(state.get("pages") or {})
        page = pages.get(terms, 0)
        update: dict = {"route": {}, "phase": "recommend"}
        conditions = f"Searching with: {needs.text() or 'no conditions'}."
        if not more and page > 0 and deps.count(profile, needs, deps.eliminated(thread_of(config))) >= MIN_STORED:
            text = (f"{conditions} I searched amazon.se for '{terms}' earlier in this conversation, and enough "
                    "stored products match, so I use them and check each pick again on amazon.se.")
        else:
            deps.say(f"Searching amazon.se for '{terms}', result page {page + 1}. This can take a few minutes.")
            report = deps.crawl(profile, needs, page + 1)
            pages[terms] = page + 1
            update["pages"] = pages
            text = f"{conditions}\n{crawl_text(report, terms, page + 1)}"
        return {**update, "messages": [assistant(text)]}

    def recommend_node(state, config):
        needs = needs_of(state)
        result = deps.picks(deps.load_profile(needs.category), needs, deps.eliminated(thread_of(config)),
                            state.get("language", "English"))
        picks = [{"listing_id": c.listing_id, "product_id": c.product_id, "title": c.title} for c in result.chosen]
        text = result.render() if picks else ("No stored product meets these needs. You can loosen a condition, "
                                              "or ask me to search more.")
        return {"picks": picks, "phase": "picks", "messages": [assistant(text)]}

    def route_node(state, config):
        route = choose_route(llm, state.get("picks", []), history_text(state))
        if route.action == "exclude" and not route.pick_numbers:
            return {"route": {"action": "stop"},
                    "messages": [assistant("Which pick do you want to remove? Give its number.")]}
        return {"route": route.model_dump()}

    def current_cards(state) -> list[Card]:
        return deps.cards([p["listing_id"] for p in state.get("picks", [])])

    def answer_node(state, config):
        allowed = user_numbers(state) | numbers_in(needs_of(state).text())
        text = answer_question(llm, current_cards(state), history_text(state), allowed,
                               state.get("language", "English"))
        return {"messages": [assistant(text or "I could not write an answer whose numbers agree with the records, "
                                               "so I do not show it. The facts are in the picks above.")]}

    def calculate_node(state, config):
        text = calculate(llm, current_cards(state), history_text(state), user_numbers(state),
                         state.get("language", "English"))
        return {"messages": [assistant(text)]}

    def exclude_node(state, config):
        route, picks = state["route"], state.get("picks", [])
        chosen = [picks[n - 1] for n in route["pick_numbers"]]
        deps.eliminate(thread_of(config), [p["product_id"] for p in chosen], route.get("reason", ""))
        names = "; ".join(p["title"][:60] for p in chosen)
        return {"messages": [assistant(f"Removed: {names}. They stay out for the rest of this conversation.")]}

    def reset_node(state, config):
        return {"needs": {}, "picks": [], "phase": "clarify"}

    graph = StateGraph(ChatState)
    graph.add_node("compact", compact_node)
    for name, fn in [("clarify", clarify_node), ("search", search_node), ("recommend", recommend_node),
                     ("route", route_node), ("answer", answer_node), ("calculate", calculate_node),
                     ("exclude", exclude_node), ("reset", reset_node)]:
        graph.add_node(name, guarded(name, fn))
    graph.add_edge(START, "compact")
    graph.add_conditional_edges("compact", lambda s: "route" if s.get("phase") == "picks" else "clarify",
                                ["clarify", "route"])
    graph.add_conditional_edges("clarify", lambda s: "search" if s.get("phase") == "search" else END,
                                ["search", END])
    graph.add_edge("search", "recommend")
    graph.add_edge("recommend", END)
    graph.add_conditional_edges("route", lambda s: s["route"]["action"], AFTER_ROUTE)
    graph.add_edge("answer", END)
    graph.add_edge("calculate", END)
    graph.add_edge("exclude", "recommend")
    graph.add_edge("reset", "clarify")
    return graph.compile(checkpointer=checkpointer)
```

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/test_graph.py -v`
Expected: 6 passed.

- [ ] **Step 6: Run all tests**

Run: `python -m pytest -q`
Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/scout/agent/state.py src/scout/agent/graph.py tests/test_graph.py
git commit -m "Conversation graph: clarify, search when stored data is not enough, recommend, follow-up actions"
```

---

### Task 10: `scout chat` with Postgres checkpoints

**Files:**
- Modify: `src/scout/db/conn.py` (`connect_checkpoints`)
- Create: `src/scout/agent/live.py`
- Modify: `src/scout/cli.py` (commands `chat` and `threads`)
- Create: `tests/test_chat_db.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_chat_db.py`:

```python
import pytest
from langgraph.checkpoint.postgres import PostgresSaver

from scout.agent.clarify import CategoryChoice, ClarifyReply
from scout.agent.graph import build_graph
from scout.config import load_settings
from scout.db.conn import connect_checkpoints
from scout.needs import Needs
from tests.fakes import FakeLLM
from tests.test_graph import World, run

pytestmark = pytest.mark.db
THREAD = "test-resume"


def test_a_thread_continues_after_a_restart():
    url = load_settings().test_database_url
    world = World()
    with connect_checkpoints(url) as conn:
        saver = PostgresSaver(conn)
        saver.setup()
        saver.delete_thread(THREAD)
        llm = FakeLLM([CategoryChoice(category="fridge"),
                       ClarifyReply(needs=Needs(), ready=False, question="Which kind?")])
        run(build_graph(world.deps(llm), saver), THREAD, "I want a fridge")
    with connect_checkpoints(url) as conn:
        saver = PostgresSaver(conn)
        graph = build_graph(world.deps(FakeLLM([])), saver)
        values = graph.get_state({"configurable": {"thread_id": THREAD}}).values
        assert values["messages"][-1]["content"] == "Which kind?"
        assert values["needs"]["category"] == "fridge"
        saver.delete_thread(THREAD)
```

- [ ] **Step 2: Run the test to see it fail**

Run: `python -m pytest tests/test_chat_db.py -v`
Expected: FAIL with `ImportError: cannot import name 'connect_checkpoints'`.

- [ ] **Step 3: Add the checkpoint connection**

Add to `src/scout/db/conn.py`:

```python
def connect_checkpoints(url: str) -> psycopg.Connection:
    """A connection for the LangGraph checkpointer. It needs autocommit and dict rows."""
    return psycopg.connect(url, autocommit=True, prepare_threshold=0, row_factory=dict_row)
```

The checkpointer creates its own tables (`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations`) in the same database. `PostgresSaver.setup()` creates them when they do not exist.

- [ ] **Step 4: Run the test**

Run: `python -m pytest tests/test_chat_db.py -v`
Expected: 1 passed.

- [ ] **Step 5: Write the live dependencies**

Create `src/scout/agent/live.py`:

```python
"""The real dependencies of the chat graph: Postgres, the amazon.se browser and the LLM."""
from scout.agent.graph import Deps
from scout.crawl import crawl as run_crawl
from scout.db.store import add_eliminations, eliminated_products
from scout.extract.page import enrich_record
from scout.extract.screen import screen_titles
from scout.picks.pipeline import make_picks, refetcher, shortlist
from scout.picks.rank import load_cards_by_ids
from scout.profiles import PROFILES_DIR, load_profile
from scout.sources.amazon_se.adapter import AmazonSeAdapter
from scout.sources.browser import BrowserSession


class LazyBrowser:
    """Opens the product-scout Chrome profile only when a step needs amazon.se."""

    def __init__(self, settings):
        self._settings, self._session, self._adapter = settings, None, None

    def adapter(self) -> AmazonSeAdapter:
        if self._adapter is None:
            self._session = BrowserSession(self._settings.browser_profile_dir).__enter__()
            self._adapter = AmazonSeAdapter(self._session, self._settings.snapshot_dir)
        return self._adapter

    def close(self) -> None:
        if self._session is not None:
            self._session.__exit__(None, None, None)
            self._session, self._adapter = None, None


def live_deps(conn, llm, browser: LazyBrowser, on_problem, say, tracer, k: int = 5) -> Deps:
    def count(profile, needs, excluded):
        return len(shortlist(conn, profile, needs, llm, excluded).ranked)

    def crawl(profile, needs, page):
        return run_crawl(conn, browser.adapter(), profile.category, needs.search_terms(profile),
                         on_problem=on_problem, first_page=page, max_pages=1, max_details=20,
                         budget=needs.budget_sek(), skip_known=True,
                         enrich=lambda rec: enrich_record(rec, profile, llm),
                         screen=lambda cands: screen_titles(cands, profile, llm, needs.wanted(profile)))

    def picks(profile, needs, excluded, language):
        short = shortlist(conn, profile, needs, llm, excluded)

        def refetch(card):  # the browser opens only when there is a card to check
            return refetcher(browser.adapter(), conn, profile.category, on_problem)(card)

        return make_picks(short, needs, k, refetch, llm, language)

    return Deps(
        llm=llm,
        profile_names=lambda: sorted(p.stem for p in PROFILES_DIR.glob("*.yaml")),
        load_profile=load_profile,
        count=count,
        crawl=crawl,
        picks=picks,
        cards=lambda ids: load_cards_by_ids(conn, ids),
        eliminate=lambda thread, ids, reason: add_eliminations(conn, thread, ids, reason),
        eliminated=lambda thread: eliminated_products(conn, thread),
        say=say,
        tracer=tracer,
    )
```

- [ ] **Step 6: Add the commands**

Add to `src/scout/cli.py`:

```python
import uuid

from langgraph.checkpoint.postgres import PostgresSaver

from scout.agent.graph import build_graph
from scout.agent.live import LazyBrowser, live_deps
from scout.db.conn import connect_checkpoints


@app.command()
def chat(thread: Optional[str] = typer.Option(None, help="the ID of a conversation to continue"),
         k: int = 5) -> None:
    """Talk with the agent. It clarifies your needs, searches amazon.se and answers follow-up questions."""
    settings = load_settings()
    tracer = tracer_from_env()
    llm = LLM(load_llm_config(), tracer=tracer)
    thread_id = thread or uuid.uuid4().hex[:8]
    config = {"configurable": {"thread_id": thread_id}}
    browser = LazyBrowser(settings)
    with connect(settings.database_url) as conn, connect_checkpoints(settings.database_url) as cp_conn:
        saver = PostgresSaver(cp_conn)
        saver.setup()
        graph = build_graph(live_deps(conn, llm, browser, ask_user, typer.echo, tracer, k), saver)
        messages = graph.get_state(config).values.get("messages", [])
        if messages:
            typer.echo(f"Continuing conversation {thread_id}. The last reply was:\n\n{messages[-1]['content']}")
        else:
            typer.echo(f"Conversation {thread_id}. To continue it later: scout chat --thread {thread_id}")
        typer.echo("Type q to stop.")
        try:
            while True:
                text = typer.prompt("\nyou", prompt_suffix="> ").strip()
                if text.lower() in {"q", "quit", "exit"}:
                    break
                before = len(graph.get_state(config).values.get("messages", []))
                with tracer.turn(thread_id, text) as span:
                    state = graph.invoke({"messages": [{"role": "user", "content": text}]}, config)
                    replies = [m["content"] for m in state["messages"][before + 1:]]
                    span.update(output="\n\n".join(replies))
                for reply in replies:
                    typer.echo(f"\n{reply}")
        finally:
            browser.close()
            tracer.flush()
    report_llm_use(llm)


@app.command()
def threads(limit: int = 20) -> None:
    """List the stored conversations, newest first."""
    with connect_checkpoints(load_settings().database_url) as cp_conn:
        PostgresSaver(cp_conn).setup()
        rows = cp_conn.execute(
            "SELECT thread_id, max(checkpoint->>'ts') AS last FROM checkpoints "
            "GROUP BY thread_id ORDER BY last DESC LIMIT %s", (limit,)).fetchall()
    for row in rows:
        typer.echo(f"{row['thread_id']}  last turn {row['last'][:16]} UTC")
```

- [ ] **Step 7: Run all tests and a short real turn**

Run: `python -m pytest -q`
Expected: all tests pass.

Run: `scout chat`, then type `I want a fridge`, then `q`.
Expected: the agent asks which kind of fridge you want. No browser window opens. Then `scout threads` shows the new thread.

- [ ] **Step 8: Commit**

```bash
git add src/scout/db/conn.py src/scout/agent/live.py src/scout/cli.py tests/test_chat_db.py
git commit -m "scout chat and scout threads: the graph with live dependencies and Postgres checkpoints"
```

---

### Task 11: The worker checks the sizes of each pick

The phase 1 check (`drop_implausible_sizes`) catches only sizes that cannot hold the volume. It did not catch the FOHERE 90 l fridge, whose page gives 48 cm height and 80 cm depth. Zheyun found this by hand on 2026-10-04. This task gives the worker the title, the bullet points and the specifications of each pick, in one call for all picks.

**Files:**
- Create: `src/scout/extract/doubt.py`
- Modify: `src/scout/picks/write.py` (show "(doubtful)")
- Modify: `src/scout/picks/pipeline.py`
- Create: `tests/test_doubt.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_doubt.py`:

```python
from scout.extract.doubt import SizeCheck, SizeDoubt, check_sizes
from scout.picks.write import format_picks
from scout.sources.base import ProductRecord
from tests.fakes import NOW, FakeLLM
from tests.test_picks_rank import card


def fohere():
    c = card(5, 4.2, 54, height=48.0, width=45.0, depth=80.0, total_volume=90.0)
    c.title = "FOHERE Kylskåp med frys 90L"
    rec = ProductRecord(source="amazon_se", source_id="B0F9Y2XN3J", url=c.url, fetched_at=NOW, title=c.title,
                        bullets=["Storlek: 48 x 45 x 80 cm (B x D x H)"])
    return c, rec


def test_a_doubt_with_a_quote_from_the_page_marks_the_sizes():
    c, rec = fohere()
    llm = FakeLLM([SizeCheck(doubts=[SizeDoubt(product=1, sizes=["height", "depth"],
                                               reason="height and depth look swapped",
                                               evidence="48 x 45 x 80 cm (B x D x H)")])])
    notes = check_sizes([(c, rec)], llm)
    assert c.attributes["height"] == {"value": 48.0, "doubtful": True}
    assert 'the page says "48 x 45 x 80 cm (B x D x H)"' in notes[5]
    assert "height: 48.0 (doubtful)" in format_picks([c], None, [], "", 0)
    assert llm.requests[0][0] == "worker"


def test_a_quote_that_is_not_on_the_page_is_ignored():
    c, rec = fohere()
    llm = FakeLLM([SizeCheck(doubts=[SizeDoubt(product=1, sizes=["height"], reason="x", evidence="Höjd: 80 cm")])])
    assert check_sizes([(c, rec)], llm) == {}
    assert "doubtful" not in c.attributes["height"]


def test_a_doubt_without_a_quote_is_the_worker_models_judgement():
    c, rec = fohere()
    llm = FakeLLM([SizeCheck(doubts=[SizeDoubt(product=1, sizes=["height"],
                                               reason="a 90 l fridge with a freezer part is taller than 48 cm")])])
    assert "judged by the worker model" in check_sizes([(c, rec)], llm)[5]


def test_cards_without_sizes_need_no_call():
    assert check_sizes([(card(1), fohere()[1])], FakeLLM([])) == {}
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `python -m pytest tests/test_doubt.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write the check**

Create `src/scout/extract/doubt.py`:

```python
"""The worker compares the sizes of each pick with the rest of its page. Sellers sometimes give wrong sizes."""
from pydantic import BaseModel, Field

from scout.extract.verify import verify_raw
from scout.picks.rank import SIZES, Card
from scout.sources.base import ProductRecord

SYSTEM = """You check the sizes of products on amazon.se pages. Sellers sometimes give wrong sizes:
the size of the box, the size of another model, or height and depth in the wrong order.
For each product, compare the sizes with the title, the bullet points and the specifications.
Report a size only when something contradicts it: a text on the page, or a clear fact about this kind of product.
For example, a 90 l fridge with a freezer part is not 48 cm high.
When a text on the page contradicts the size, copy that text exactly into evidence. Otherwise leave evidence empty.
Most products have no problem. Return no doubt for them."""


class SizeDoubt(BaseModel):
    product: int
    sizes: list[str]
    reason: str
    evidence: str = ""


class SizeCheck(BaseModel):
    doubts: list[SizeDoubt] = Field(default_factory=list)


def page_text(rec: ProductRecord) -> str:
    specs = "\n".join(f"{k}: {v}" for k, v in rec.specs.items())
    return "\n".join([rec.title or "", *rec.bullets, specs])


def product_block(n: int, card: Card, rec: ProductRecord) -> str:
    facts = ", ".join(f"{k}={v.get('value')} {v.get('unit') or ''}".strip() for k, v in card.attributes.items())
    return f"Product {n}: {card.title}\nFacts: {facts}\nPage:\n{page_text(rec)[:3000]}"


def check_sizes(items: list[tuple[Card, ProductRecord]], llm) -> dict[int, str]:
    """Mark doubtful sizes on the cards and return a note for each card. The values never change."""
    checked = [(card, rec) for card, rec in items if any(s in card.attributes for s in SIZES)]
    if not checked:
        return {}
    prompt = "\n\n".join(product_block(n, card, rec) for n, (card, rec) in enumerate(checked, 1))
    notes: dict[int, str] = {}
    for doubt in llm.structured("worker", SYSTEM, prompt, SizeCheck).doubts:
        if not 1 <= doubt.product <= len(checked):
            continue
        card, rec = checked[doubt.product - 1]
        sizes = [s for s in SIZES if s in doubt.sizes and s in card.attributes]
        if not sizes:
            continue
        if doubt.evidence and not verify_raw(doubt.evidence, page_text(rec) + "\n" + rec.text):
            continue  # the quote is not on the page, so the doubt has no support
        source = f'the page says "{doubt.evidence}"' if doubt.evidence else "judged by the worker model"
        card.attributes = {k: ({**v, "doubtful": True} if k in sizes else v) for k, v in card.attributes.items()}
        notes[card.listing_id] = f"{card.title[:60]}: {', '.join(sizes)} may be wrong ({doubt.reason}; {source})."
    return notes
```

- [ ] **Step 4: Show doubtful values**

In `src/scout/picks/write.py`, add:

```python
def attribute_text(entry: dict) -> str:
    text = f"{entry.get('value')} {entry.get('unit') or ''}".strip()
    return f"{text} (doubtful)" if entry.get("doubtful") else text
```

In `_card_json`, use it for the attributes:

```python
        "attributes": {k: attribute_text(v) for k, v in card.attributes.items()},
```

In `format_picks`, change the line that builds `attrs`:

```python
        attrs = ", ".join(f"{k}: {attribute_text(v)}" for k, v in card.attributes.items())
```

- [ ] **Step 5: Check the sizes in the pipeline**

In `src/scout/picks/pipeline.py`, import `check_sizes` from `scout.extract.doubt`. Then change `make_picks`. It keeps the fresh records of the recheck, and it checks the sizes before it writes the summaries.

```python
def make_picks(short: Shortlist, needs: Needs, k: int, refetch: Refetch, llm,
               language: str = "English") -> PicksResult:
    """Recheck the top cards on the live pages, check their sizes, then write summaries with checked numbers."""
    fetched: dict[int, ProductRecord] = {}

    def refetch_and_keep(card: Card) -> ProductRecord | None:
        rec = refetch(card)
        if rec is not None:
            fetched[card.listing_id] = rec
        return rec

    chosen, recheck_notes = recheck_picks(short.ranked, k, refetch_and_keep, needs.budget_sek(),
                                          needs.rank_conditions())
    notes = short.notes + recheck_notes + [short.size_notes[c.listing_id] for c in chosen
                                           if c.listing_id in short.size_notes]
    texts, errors = None, []
    if chosen and llm is not None:
        try:
            doubts = check_sizes([(c, fetched[c.listing_id]) for c in chosen if c.listing_id in fetched], llm)
            notes += [doubts[c.listing_id] for c in chosen if c.listing_id in doubts]
        except (LLMUnavailable, LLMOutputError) as exc:
            notes.append(f"Size check skipped: {exc}")
        try:
            texts, errors = write_picks(llm, chosen, needs.text(), language)
        except LLMUnavailable as exc:
            notes.append(f"No summaries: {exc}")
    return PicksResult(chosen, texts, errors, notes, short.dropped, needs.text())
```

- [ ] **Step 6: Run all tests**

Run: `python -m pytest -q`
Expected: all tests pass.

- [ ] **Step 7: Check the FOHERE page for real**

Run: `scout picks fridge --budget 9000 --where "height<=186" --where "width<=60"`
Expected: if the FOHERE 90 l fridge (B0F9Y2XN3J) is a pick, its height or depth shows "(doubtful)", and a note gives the reason. Write the result under this step, also when the worker does not flag it.

- [ ] **Step 8: Commit**

```bash
git add src/scout/extract/doubt.py src/scout/picks tests/test_doubt.py
git commit -m "Worker size check on the picks: doubtful sizes are marked, quotes must be on the page"
```

---

### Task 12: Read the price when amazon.se hides it

**Why:** Zheyun reviewed the 32 labels that Claude made, on 2026-10-04. 27 were correct. 5 were wrong, and in all 5 the page shows no single price:

1. **Offer panel (4 pages).** B07YKSGPV1, B093388SWG, B0B5LN7J3X and B0DRVXYFXD show no buy box. The page says "Priset är högre än vanligt" and shows the button "Visa alla köpalternativ". The offer panel behind the button has the prices. The panel does not show the stock. Zheyun's rule: an offer that you can add to the cart is in stock.
2. **Size choice (1 page).** B0H4YNM2BL shows a price range, 1809 to 1977 kr, and "Välj Storlek". The page shows a price and a stock only after a size is selected. The page has an ASIN for each colour and size (`dimensionValuesDisplayData`, here 4 ASINs for 2 colours and 2 sizes).

Now these products have no price, so a budget removes them from the picks, although they can be bought. The labels and the parser both said "no price", so the evaluation counted them as correct.

**Note.** The dry run did not include this task. The saved pages do not contain the offer panel: it loads only after a click (the saved pages have the class `aod-assets-not-loaded`). So the selectors and the test fixtures come from real pages that Step 1 saves. Claude writes the code of Steps 2 to 6 after Step 1, with the same rules as the other tasks: a test first, then the code.

**Files:**
- Modify: `src/scout/sources/base.py`, `src/scout/sources/amazon_se/product.py`, `src/scout/sources/amazon_se/adapter.py`, `src/scout/sources/browser.py`
- Create: `src/scout/sources/amazon_se/offers.py`, `tests/fixtures/amazon_se/offers_*.html`, `tests/fixtures/amazon_se/product_size_*.html`, `tests/test_amazon_offers.py`
- Modify: `src/scout/crawl.py`, `src/scout/picks/recheck.py`, `src/scout/picks/write.py`, `src/scout/db/schema.sql`, `src/scout/db/store.py`

- [ ] **Step 1: Save real pages after the click**

Add `--offers` and `--sizes` to `scout snap`.
- With `--offers`, the browser opens the product page, clicks "Visa alla köpalternativ", waits up to 10 seconds for `#aod-container`, and saves the panel HTML and a screenshot in `snapshots/amazon_se/offers/<ASIN>/`.
- With `--sizes`, the browser clicks each option in the row "Storlek" (at most 4), waits for the price to change, and saves the page HTML and a screenshot after each click in `snapshots/amazon_se/product/<ASIN>/`.

Run `--offers` on the 4 offer-panel ASINs and `--sizes` on B0H4YNM2BL. Zheyun is at the computer, because a captcha can come. Copy reduced versions to `tests/fixtures/amazon_se/` (remove scripts and images, keep the offer blocks and the price blocks). One offer fixture must have a used offer, if a real panel has one.

- [ ] **Step 2: Detect a hidden price**

`ProductRecord` gets `price_hidden: Literal["offers", "size"] | None = None` and `price_source: Literal["buy_box", "all_offers", "size_choice"] | None = None`.
- `offers`: the parser finds no price and finds `#buybox-see-all-buying-choices`.
- `size`: the parser finds `.a-price-range` and a row with "Välj". It also reads the ASIN, colour and size of each option from `dimensionValuesDisplayData`.

Test this on the 38 labelled pages: the 4 offer pages give `offers`, B0H4YNM2BL gives `size`, and the other 33 give None.

- [ ] **Step 3: Parse the offer panel**

`parse_offers(html) -> list[Offer]`. `Offer` has `price`, `currency`, `condition` ("new" or "used"), `seller`, `delivery` (text, for example "Gratis leverans"), `can_add_to_cart` and `raw_text`. Python finds each `raw_text` on the panel, as for the other extracted values. `lowest_new(offers)` returns the cheapest offer in the condition "new" that can go in the cart, or None. Test: the fixture with a used offer never returns the used price.

- [ ] **Step 4: Use the offer panel and the size choice in the crawl and the recheck**

- `offers`: the adapter opens the panel in the same tab (one click, no new page load). The record takes the price of `lowest_new`, with `price_source="all_offers"`, and `in_stock` is True. When the panel has no new offer that can go in the cart, the price stays empty, as now.
- `size`: the adapter clicks each size of the colour on the page, at most 4. Each size becomes one row in `variants`, with its own price and stock and `price_source="size_choice"`. The variant key is the ASIN of the option, and the label is the colour and the size. The budget filter keeps the listing when one variant is in the budget. The picks show the variant that is in the budget.
- The fetch log gets one line for each click. `variants` and `price_history` get the column `price_source`. The recheck of the picks does the same clicks.

- [ ] **Step 5: Show it in the picks**

- A card with `price_source="all_offers"` shows "(from all buying options, Amazon marks this price as higher than usual)" after the price.
- A card with `price_source="size_choice"` shows the size after the price, for example "1 809,00 kr (size 50 L)".

`check_picks` accepts the price as before. Test with two cards in `tests/test_picks_write.py`.

- [ ] **Step 6: New labels and a new evaluation run (Zheyun)**

Zheyun labels the 4 offer panels (the cheapest new offer that can go in the cart, in kr) and each size of B0H4YNM2BL (price and stock), from the screenshots of Step 1. Add these rows to `data/eval/price_labels_all.csv` with the note "labelled by Zheyun after the click". Run `scout eval-prices`. Write the result under this step.

- [ ] **Step 7: Commit**

```bash
git add src tests
git commit -m "Read the price from the offer panel or the size choice when amazon.se hides it"
```

---

### Task 13: Langfuse setup (Zheyun)

- [ ] **Step 1: Choose where the traces go**

Claude recommends **Langfuse Cloud, EU region, free Hobby plan**. A self-hosted Langfuse (version 3 and later) needs Postgres, ClickHouse, Redis and S3-compatible storage, which is a heavy load on the laptop. The traces contain the conversation and the product facts. This is the same kind of data that already goes to Gemini and Groq. Check the limits of the Hobby plan on the pricing page on the day that you sign up, and write them here. Never add a payment card.

- [ ] **Step 2: Create the keys**

1. Sign up at https://cloud.langfuse.com (EU). Use Google or GitHub.
2. Create an organisation and a project named `product-scout`.
3. In the project settings, open **API Keys** and create a key pair.
4. Add three lines to `.env`:

```
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_BASE_URL=https://cloud.langfuse.com
```

- [ ] **Step 3: Check the keys**

Run: `scout trace-ping`
Expected: `keys accepted: True`. In Langfuse, **Tracing** shows a trace named "chat turn" in the session "trace-ping".

---

### Task 14: Real conversations (Zheyun) and the numbers

- [ ] **Step 1: Conversation A, a fridge with a freezer part**

Run `scout chat`. Write these turns in your own words, in Chinese or English:

1. You want a fridge. The space is 60 cm wide and 186 cm high. The budget is 9000 kr.
2. Answer the questions of the agent. Say that you want a freezer part.
3. After the picks: ask how tall pick 1 is.
4. Reject one pick and give a reason.
5. Ask for the price of pick 1 plus 499 kr for delivery.
6. Type `q`.

Check: the agent asked for the kind; the picks have no mini fridge, cool box or accessory; each number in each answer is on the card or in your messages; the rejected product does not come back; the calculation shows a formula.

- [ ] **Step 2: Continue conversation A**

Run `scout threads`, then `scout chat --thread <id>`. Ask for more products. Check: the agent remembers the needs and the rejected product, and it reads result page 2.

- [ ] **Step 3: Conversation B, a mini fridge**

Run `scout chat` and ask for a small fridge for an office desk, budget 1500 kr. Check: the search uses the mini fridge terms, and the picks are mini fridges.

- [ ] **Step 4: Conversation C, a category without a profile**

Ask for headphones. Check: the agent says which categories it can search now.

- [ ] **Step 5: Measure (Claude)**

From the terminal output and from Langfuse, write a table for conversations A and B:
- turns, and LLM calls and tokens for each model;
- seconds for each turn (median and maximum), and which step took the most time;
- product pages fetched;
- answers and summaries that failed the number check in their first round, and how many passed later;
- size doubts, and for each one whether Zheyun agrees after she looks at the page.

Report only the real numbers. Nothing goes into the CV until Zheyun confirms it.

- [ ] **Step 6: Update the documents**

1. Spec: write the eight decisions of this plan into sections 3, 4.1, 4.2, 4.3, 4.7, 4.8 and 4.11. In the table of LLM candidates, change the Groq row: it is the `chat` fallback, with the limits from 2026-10-04.
2. Diagram (https://claude.ai/artifact/4jJqCkVudUFU24kTyXsiqZ): mark the phase 2 nodes as built and update the Progress section.
3. Write the results of Steps 1 to 5 under this task.

- [ ] **Step 7: Commit**

```bash
git add docs
git commit -m "Phase 2 real conversations: results, spec and plan notes"
```

---

## After phase 2

- Phase 3 (text and retrieval) has its own plan. It adds `search_text` and `extract_attribute`, so the agent can answer questions that the cards cannot answer.
- Use the filters of amazon.se in the search (spec section 4.2, predicate pushdown). The saved "kylskåp" pages show these filters: price, 4 stars and up, condition, Konfiguration (with or without a freezer part, and where), Kapacitet, Höjd, Bredd and Djup in wide ranges, and the brand. Not done yet. A filter can hide a product that Amazon did not tag, so first measure how many of the products that meet the needs each filter keeps.
- Measure how many result pages give good picks: after which page do the top 5 stop changing? Zheyun decided on 2026-10-04 to do this after the reviews are connected, because then the candidates come from the search and from professional reviews.
- Store the title screening results, so that `--no-llm` runs and later searches do not screen the same titles again (Zheyun decided on 2026-10-04 to keep the LLM screening for now).
