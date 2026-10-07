# Phase 2 Conversation Implementation Plan

> **How to use this plan.** Do the tasks in order. Each step has a checkbox (`- [ ]`). Mark the step when it is done. Commit at the end of each task.

**Goal:** Replace the one-shot `scout picks` command with a conversation in the terminal. The agent clarifies the needs of the user, searches amazon.se when the stored data is not enough, shows the picks, and handles follow-up turns: questions, calculations, rejected picks, changed needs and "show me more". The user can stop and continue a conversation later. Langfuse records each turn.

**Architecture:** A LangGraph `StateGraph` runs one turn for each user message. A Postgres checkpointer keeps the state of each thread. The state keeps only product IDs and the needs, never full records. Each node uses code from phase 1: the picks steps move into one module (`scout/picks/pipeline.py`) that the command and the graph share. Before the picks, the chat model fills the needs through structured output. After the picks, it acts through native tool calls: it can remove picks, bring them back, ask, answer, and start the next step, several of these in one reply. Python validates each call, does all filters and all arithmetic, and checks each number in each reply against the records. The worker model screens titles for the kind of product that the user wants and checks the sizes on each pick against the rest of the page.

**Tech Stack:** Phase 1 stack, plus `langgraph` 1.2, `langgraph-checkpoint-postgres` 3.1 (`PostgresSaver`) and `langfuse` 4.16. I installed these three on Python 3.10 in a scratch environment on 2026-10-04 and checked the APIs that this plan uses: `StateGraph`, conditional edges, nodes that take `(state, config)`, `InMemorySaver`, `PostgresSaver(conn).setup()`, `Langfuse.start_as_current_observation`, `propagate_attributes(session_id=...)`.

**Spec:** `docs/superpowers/specs/2026-10-02-product-scout-design.md`, sections 3, 4.7 to 4.10, and phase 2 in section 9.

**Video:** a walkthrough of this plan, with captions and voice, is at https://claude.ai/artifact/BRmWUvhE8E82DBwQQYeLNx. Fig. 4 of the diagram (https://claude.ai/artifact/4jJqCkVudUFU24kTyXsiqZ) shows one turn of the conversation.

**Done in the repository.** On 2026-10-05 I did Tasks 1 to 12 in the repository, one commit for each task, and 233 tests passed. The repository then had the same files as a fresh build from this plan, except the new dependencies in `pyproject.toml` and the order of the imports in `cli.py`. The steps "Run the tests to see them fail" stay open: the code and its tests came from the dry runs, so I added them together. Four steps stay open because they need a real browser or a chat quota: Task 3 Step 7, Task 10 Step 7, Task 11 Step 7 and Task 12 Step 7.

**Code review.** After Task 12, a code review on 2026-10-05 found that some numbers of `scout eval-chat` were not correct. I changed the code after the commit of Task 12, so the code blocks of this plan show the earlier version of these parts. The changes are:

- Each checked step (clarify, summaries, actions, calculations) marks its last call record as passed or not passed (`LLM.mark_checked`). The evaluation reads this mark. Before, it looked for one failure sentence in the replies, so a step that stopped with an exception counted as passed.
- A model that gives no usable reply (`LLMOutputError`) now gets its own message. It is a model failure and counts in the score. An outage (quota, server) or a program error makes the turn "not measured". Its expected values and steps do not count. Calls, tokens and time still count.
- Turn 1 of each script that refers to picks checks the 5 pick titles (`picks` in `Expect`). When they differ, the later turns of that script are not measured, because the worker model, not the chat model, changed the picks.
- `scout eval-chat` ends the database transaction after each turn, as `scout chat` does. Before the run, it stops on an unknown provider, a missing API key or an unknown script id.
- `scout chat` and `scout eval-chat` build their dependencies with one function (`shared_deps` in `agent/live.py`), so the evaluation runs the same picks pipeline. The evaluation does not check the sizes, because the stored records do not hold the page text, and the picks say so.

237 tests passed after these changes.

**Dry run.** On 2026-10-04 I applied all code of this plan in a temporary Git worktree, on commit 336f61b. 204 tests passed: the 163 tests of phase 1 and 41 new ones. Then I ran one real clarify turn of `scout chat` in Chinese ("a fridge, 60 cm wide, 186 cm high, budget 9000 kronor"). The agent stored width<=60, height<=186 and budget 9000. It asked about the kind (with or without a freezer part), the depth and the noise. No browser opened. The chat role used 2 calls on Groq, because the Gemini chat quota was used up. I then deleted that thread and the worktree. Later on 2026-10-04, I added decisions 7 and 8. I applied the changed plan again in a new worktree: 207 tests passed (163 and 44 new). On 2026-10-05 I reviewed the plan. Decisions 9 to 13 and Task 12 come from that review. I applied the plan a third time: 220 tests passed (163 and 57 new), and Task 12 ran the 12 scripted conversations with Nemotron 3 Ultra. The Gemini chat quota ran out after the first script. The results are under Task 12. Later on 2026-10-05, the scripts showed that every tested model guesses or removes too much when the shopper's words do not name one pick. I then decided that the chat model acts through tool calls after the picks (decision 1), with the checks of decision 14 and an undo (decision 15). I applied the changed plan in a fourth worktree: 233 tests passed (163 and 70 new), and Task 12 ran 24 scripted conversations. Task 13 was not part of any run.

## Global Constraints

- All constraints of the phase 1 plan stay. The most important ones for this phase:
  - Python 3.10. Price is only a budget filter. The LLM never writes SQL. The user logs in and solves captchas.
  - Each number that the agent shows comes from a record, from the user, or from a Python calculation. Python checks this before the agent shows the text.
  - Use only free tiers. Never enable billing on Gemini, Groq or Langfuse.
- The conversation state keeps product IDs, the needs and the messages. Tools get the product data from Postgres when a step needs it.
- A conversation that only clarifies the needs must not open the browser.
- The agent writes all its messages in English. The shopper can write in any language.
- Langfuse is optional. Without `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY`, all tracing calls do nothing, and the program prints no warning.
- The tests never call a real LLM, a real browser or Langfuse. They use `FakeLLM`, fake dependencies and `InMemorySaver`. Only tests with the mark `db` use the Postgres container.

## Decisions for this phase

These points differ from the spec or make it more exact. Task 15 writes them into the spec.

1. **After the picks, the chat model acts through native tool calls.** I decided this on 2026-10-05, because shoppers write freely: two things in one message, an undo, a question about the order. One fixed action per turn (the first version of this plan) could not do these, and it made the model guess when it had to ask. The model gets four tools (Task 7): `remove_picks`, `restore_picks`, `next_step` and `reply`, and it sees the full cards. It can call several tools in one reply. Python checks each call before anything changes, and it writes the text about what changed. The clarify step stays structured output, because Python checks each field of the needs. The spec tools map as follows. `query_products`: `next_step` `change_needs`, then the SQL and Python filters. `exclude_and_rerank`: `remove_picks`. `fetch_more`: `next_step` `show_more`. `calculate`: `next_step` `calculate`. `recheck_availability`: `next_step` `recheck`. `search_text` and `extract_attribute` need chunks and come in phase 3. `update_profile` comes in phase 4, with profile generation.
2. **Login and captcha pauses stay a prompt in the terminal.** The graph runs in the same process as the terminal, so the crawl can wait for the user, as in phase 1. The LangGraph interrupt comes in phase 7, when a web page replaces the terminal.
3. **Profiles get kinds.** The 5 l cosmetics cooler ranked second in the phase 1 runs, because "fridge" was too wide. A profile now lists kinds (for the fridge: with a freezer part, without one, mini fridge, wine cooler, cool box), each with Swedish search terms. The clarify step must know the kind before it searches. The title screening keeps only items of that kind.
4. **The clarify step must not invent numbers.** Each condition and the budget must agree with a number that the user wrote, with a unit change of a power of ten at most (1,8 m is 180 cm). Otherwise the model must ask.
5. **Size doubts are marked, never changed.** The worker compares the sizes on each pick with the title, the bullet points and the specifications. A doubtful size gets "(doubtful)" and a note. A quote in the note must be on the page.
6. **A hidden price comes from one click on the page.** I found two cases in my label review on 2026-10-04 (5 of 38 labelled pages). On 4 pages, amazon.se shows no buy box, only "Priset är högre än vanligt" and the button "Visa alla köpalternativ". The program opens the offer panel and reads the lowest new offer that can go in the cart. On 1 page, the price shows only after a size is selected. The program opens each size by its own ASIN and stores it as its own listing, because each size has its own measurements and price. Each price keeps its source (`all_offers` or `size_choice`), and the picks show it.
7. **Each thread searches one time before it uses the stored products.** I asked on 2026-10-04: stored data gets old. So the first search for a set of search terms in a thread always reads result page 1 on amazon.se. Products that are already stored get the rating and the review count of the result card, and the browser does not open them. Only new products get a product page. When all results are already stored, the search costs one page load. After that, the thread uses the stored products while 10 or more meet the needs. The thread also reads page 1 again when the budget is higher than in the earlier search for these words, because the crawl does not store results over the budget.
8. **A failed check tells the model what is wrong and what is right.** I asked for this on 2026-10-04. The next round gets the last answer of the model and each problem with the value from the record, for example "height is 185, not 186", or the numbers on the card for a number that has no claim. After the last round, only the picks that still fail lose their summary. The calculation tool gets the same feedback.
9. **The agent writes English.** I decided this on 2026-10-05. The shopper can write in Chinese, Swedish or English. The questions, the answers and the fixed texts are in English. The state has no language field.
10. **A follow-up changes only what it must.** "More" leaves out the products that the thread showed since the needs last changed. "Exclude" keeps the other picks without a new page load and adds new ones. "Recheck" checks the shown picks first and replaces the ones that fail. A kept pick keeps its size doubt and its note.
11. **Only lengths and weights can change unit.** 1,8 m is 180 cm, and 600 mm is 60 cm. The budget and the other attributes must use a number that the shopper wrote: "2 people" is not 200 l, and "60 cm" is not a budget of 6000. "9k", "9 tusen", "9千" and "1万" count as written numbers.
12. **A problem in one step ends the turn, not the conversation.** Each node catches all errors and gives a short message. The chat loop catches the rest, closes the database transaction and waits for the next message.
13. **Scripted conversations measure the agent.** `scout eval-chat` runs 24 scripts with the real chat model over the stored products, without the browser. Python compares the needs, the next step, the removed picks and other values with the values that the script expects, and it counts the check rounds of each step. A new chat model must pass this run before it goes into `config/models.yaml`.
14. **The model never guesses which pick the shopper means.** `remove_picks` takes every pick that fits the shopper's words, the words themselves, and `one_pick`: true when the words name one pick. When several picks fit but the shopper means one ("the Bomann" with three Bomann picks), or no pick fits ("that one" with no earlier pick), Python asks "Which pick do you mean" and lists the candidates. The words must be in the shopper's last message. A reply that says that products were removed or brought back needs the tool call that did it. In the scripts of 2026-10-05, without these rules, Groq removed a guessed pick or all candidates in 3 of 3 unclear cases, and Nemotron in 1 of 3.
15. **A removal can be undone.** `restore_picks` brings back products that the shopper removed in this thread. They are checked first and shown with the kept picks.

## Review Focus

1. The clarify step gets "höjd 186" and returns height 185. Python must send it back, and the final needs must say 186. Test in Task 6.
2. A follow-up answer has a number that is not in its claims, not in the user's messages and not a pick number. The agent must not show it. Test in Task 7.
3. The user rejects pick 1. Pick 1 must stay out for the rest of the thread, also after a restart. Tests in Tasks 4, 9 and 10.
4. The calculation tool gets an exchange rate that the user did not give. It must ask for the rate and calculate nothing. Test in Task 8.
5. A conversation that only clarifies never calls the crawl and never opens the browser. Test in Task 9.
6. The size check returns a quote that is not on the page. The size must stay as it is, with no note. Test in Task 11.
7. A product page has no buy box and the offer panel has only used offers. The price must stay empty. A used offer must never become the price. Test in Task 13.
8. A new thread starts with 20 stored products that meet the needs. The agent must still read result page 1 one time, and it must not open the product pages of stored products. Tests in Tasks 4 and 9.
9. A summary has a number that is not in its claims. The retry prompt must contain the last answer and the numbers on that card. After three failed rounds, the other picks keep their summaries. Test in Task 3.
10. The shopper raises the budget from 9000 to 12000 with the same search words. The agent must read result page 1 again. A lower budget must not start a search. Test in Task 9.
11. The shopper asks for more. The new picks must not repeat the products that the thread showed. Test in Task 9.
12. The shopper rejects pick 1. Pick 2 stays without a new page load, and it keeps its "(doubtful)" mark and its note. Tests in Tasks 3 and 9.
13. The browser fails during a search. The turn ends with a message, the picks step does not run, and the needs stay. A failed SQL statement does not block the next turn. Tests in Tasks 9 and 10.
14. The shopper writes "a family of 4". A condition of 400 l or a budget of 4000 must go back to the model. Tests in Tasks 2 and 5.
15. The shopper writes "Remove that one" with no number. No reply may say that a pick was removed. The script `en-unclear-exclude` of Task 12 checks this with a real model.
16. Three picks are Bomann, and the shopper writes "Remove the Bomann". The model returns all three with `one_pick` true. Python must ask which one and remove nothing. Tests in Tasks 7 and 9, and the script `en-unclear-brand`.
17. A reply says "Removed pick 3" without a `remove_picks` call. It must go back to the model, and after three rounds nothing changes. Tests in Tasks 7 and 9.
18. The shopper removes pick 1 and then wants it back. It must leave the eliminations of this thread, and the other threads keep theirs. Tests in Tasks 4 and 9.

---

## File Structure

```
pyproject.toml                       add langgraph, langgraph-checkpoint-postgres, langfuse
config/models.yaml                   add the nvidia provider
eval/chat/conversations.yaml         24 scripted conversations with the expected values
profiles/fridge.yaml                 add search_terms_sv and kinds
src/scout/
  trace.py                           NoTracer, LangfuseTracer, tracer_from_env
  llm/feedback.py                    retry_prompt: the last answer and each problem
  needs.py                           Needs, ConditionSpec, check_needs, was_said
  profiles.py                        add ProductKind, CategoryProfile.kinds and .kind()
  crawl.py                           add first_page, skip_known, CrawlReport.known
  cli.py                             picks uses the pipeline; add chat, threads, trace-ping, eval-chat
  llm/client.py                      a tracer records each attempt; each call records its schema; tool calls
  eval/chat.py                       run and score the scripted conversations
  extract/screen.py                  screen_titles takes the wanted kind
  extract/doubt.py                   worker size check with checked quotes
  sources/amazon_se/offers.py        parse the offer panel (Visa alla köpalternativ)
  sources/amazon_se/product.py       detect a hidden price: offer panel or size choice
  db/schema.sql                      add eliminations
  db/conn.py                         add end_transaction, connect_checkpoints
  db/store.py                        add known_source_ids, refresh_listings, add_eliminations, eliminated_products,
                                     restore_products
  picks/pipeline.py                  shortlist (screens a title one time), make_picks (kept and first cards)
  picks/rank.py                      add load_cards_by_ids
  picks/write.py                     shared claim checks; "(doubtful)" in the output
  agent/
    __init__.py
    state.py                         ChatState
    history.py                       history_text, user_numbers, compact
    clarify.py                       pick_category, clarify
    followup.py                      the follow-up tools, check_act, act
    calc.py                          CalcPlan, run_plan, calculate
    graph.py                         Deps, build_graph
    live.py                          LazyBrowser, live_deps
tests/
  fakes.py                           add FakeTracer
  test_trace.py, test_needs.py, test_picks_pipeline.py, test_history.py, test_clarify.py,
  test_llm_tools.py, test_followup.py, test_calc.py, test_graph.py, test_chat_db.py, test_doubt.py, test_eval_chat.py
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

- [x] **Step 1: Add the dependencies**

In `pyproject.toml`, add these lines to `dependencies`:

```toml
  "langfuse>=4.16,<5",
  "langgraph>=1.2,<2",
  "langgraph-checkpoint-postgres>=3.1,<4",
```

Run: `uv pip install -e ".[dev]"`
Expected: the three packages and their dependencies (`langchain-core`, `opentelemetry-sdk`, `psycopg-pool`) install.

- [x] **Step 2: Write the failing tests**

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

In `tests/fakes.py`, import `CallRecord` from `scout.llm.client`. Then replace the class `FakeLLM`, so that it records each call as the real client does:

```python
class FakeLLM:
    """Returns queued replies (or raises queued exceptions) and records the prompts."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []
        self.calls = []

    def structured(self, role, system, user, schema):
        self.requests.append((role, user))
        self.calls.append(CallRecord(role, f"fake-{role}", 0, 0, 0.0, schema.__name__))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def model_for(self, role):
        return f"fake-{role}"
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


def test_each_call_records_its_schema_and_whether_the_reply_matched():
    llm = make(['{"wrong": 1}', '{"answer": "pong"}'], FakeTracer())
    llm.structured("chat", "sys", "user", Answer)
    assert [(c.schema, c.valid) for c in llm.calls] == [("Answer", False), ("Answer", True)]


def test_a_hung_request_stops_after_the_timeout(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    client = LLM(CONFIG).client("gemini")
    assert (client.timeout, client.max_retries) == (LLM.TIMEOUT, 0)
```

- [ ] **Step 3: Run the tests to see them fail**

Run: `python -m pytest tests/test_trace.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scout.trace'`.

- [x] **Step 4: Write the tracer**

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

- [x] **Step 5: Record each LLM attempt**

In `src/scout/llm/client.py`, add two fields at the end of `CallRecord`. The scripted conversations of Task 12 count the check rounds of each step with them.

```python
    schema: str = ""
    valid: bool = True  # False when the reply did not match the schema
```

Import the tracer:

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
            record = CallRecord(role, role_cfg.model, tokens_in, tokens_out, self._clock() - started, schema.__name__)
            self.calls.append(record)
            try:
                return schema.model_validate_json(strip_fences(text))
            except ValidationError as exc:
                record.valid = False
                ...  # unchanged
```

In `LLM`, add `TIMEOUT = 120  # seconds for one request` after `MAX_ATTEMPTS`, and create each client with a timeout and without the retries of the OpenAI library:

```python
            # A hung request counts as unavailable after TIMEOUT seconds. _structured does its own retries.
            self._clients[provider] = OpenAI(base_url=settings.base_url, api_key=key, timeout=self.TIMEOUT,
                                             max_retries=0)
```

The library waits 600 seconds and tries 2 more times by default, so one hung request could stop a chat turn for 30 minutes. This happened in a scripted run on 2026-10-05. A timeout is an `APIConnectionError`, so `_structured` already treats it as "unavailable".

- [x] **Step 6: Add `scout trace-ping`**

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

- [x] **Step 7: Run all tests**

Run: `python -m pytest -q`
Expected: all tests pass, including the 5 new ones.

- [x] **Step 8: Commit**

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

- [x] **Step 1: Write the failing tests**

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

from scout.needs import UNIT_FACTORS, ConditionSpec, Needs, check_needs, was_said
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


def test_only_lengths_and_weights_allow_a_unit_change():
    said = {Decimal("1.8"), Decimal("600"), Decimal("60"), Decimal("2")}
    assert was_said(180, said, UNIT_FACTORS["cm"])      # 1,8 m
    assert was_said(60, said, UNIT_FACTORS["cm"])       # 600 mm
    assert not was_said(185, said, UNIT_FACTORS["cm"])
    assert not was_said(200, said)                      # "2 people" is not 200 l
    assert not was_said(6000, said)                     # "60 cm" is not a budget of 6000


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
    volume = needs(conditions=[ConditionSpec(attribute="total_volume", op=">=", value=200)])
    assert any("total_volume 200 is not a number the shopper wrote" in e
               for e in check_needs(volume, FRIDGE, said={Decimal("2")}))
    assert check_needs(n.model_copy(update={"kind": None, "conditions": [], "budget": None}), FRIDGE) == []
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `python -m pytest tests/test_needs.py tests/test_profiles.py tests/test_screen.py -v`
Expected: FAIL. `scout.needs` does not exist, and the profile has no `kinds`.

- [x] **Step 3: Add kinds to the profile model**

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

- [x] **Step 4: Write the needs model**

Create `src/scout/needs.py`:

```python
"""What the shopper wants: the kind of product, hard conditions, the budget and soft preferences."""
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from scout.picks.rank import Condition
from scout.profiles import CategoryProfile

# A number counts as said when the shopper wrote it, or wrote it in another unit of the same length or weight.
# Other units and the budget allow no change: "2 people" is not 200 l, and "60 cm" is not a budget of 6000.
UNIT_FACTORS = {
    "cm": (Decimal("1"), Decimal("0.1"), Decimal("100")),    # cm, mm, m
    "mm": (Decimal("1"), Decimal("10"), Decimal("1000")),    # mm, cm, m
    "kg": (Decimal("1"), Decimal("0.001")),                  # kg, g
}
SAME = (Decimal("1"),)


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


def was_said(value: float, said: set[Decimal], factors: tuple[Decimal, ...] = SAME) -> bool:
    target = Decimal(str(value))
    return any(number * factor == target for number in said for factor in factors)


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
        elif said is not None and not was_said(cond.value, said, UNIT_FACTORS.get(attr.unit or "", SAME)):
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

- [x] **Step 5: Screen for the wanted kind**

In `src/scout/extract/screen.py`, change `screen_titles`:

```python
def screen_titles(cands: list[T], profile: CategoryProfile, llm, wanted: str | None = None) -> list[T]:
    lines = "\n".join(f"{n}: {c.title}" for n, c in enumerate(cands))
    user = f"The shopper wants: {wanted or profile.display_name}\n\nResults:\n{lines}\n\nReturn the numbers to keep."
    keep = set(llm.structured("worker", SYSTEM, user, Keep).keep)
    return [c for n, c in enumerate(cands) if n in keep]
```

- [x] **Step 6: Run all tests**

Run: `python -m pytest -q`
Expected: all tests pass.

- [x] **Step 7: Commit**

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

- [x] **Step 1: Write the failing tests**

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


def test_a_title_is_screened_one_time(db):
    stock(db)
    screened = {}
    llm = FakeLLM([Keep(keep=[0, 1])])
    needs = Needs(category="fridge", kind="fridge_freezer")
    first = shortlist(db, FRIDGE, needs, llm, screened=screened)
    again = shortlist(db, FRIDGE, needs, llm, screened=screened)
    assert len(llm.requests) == 1 and [c.listing_id for c in again.ranked] == [c.listing_id for c in first.ranked]


def test_kept_cards_need_no_new_check_and_first_cards_are_checked_first(db):
    stock(db)
    needs = Needs(category="fridge")
    short = shortlist(db, FRIDGE, needs, None)
    top, second = short.ranked
    checked = []

    def counting(card):
        checked.append(card.listing_id)
        return refetch(card)

    kept = make_picks(short, needs, 2, counting, None, kept=[top])
    assert [c.listing_id for c in kept.chosen] == [top.listing_id, second.listing_id]
    assert checked == [second.listing_id]
    checked.clear()
    make_picks(short, needs, 2, counting, None, first=[second])
    assert checked == [second.listing_id, top.listing_id]
    checked.clear()
    back = make_picks(short, needs, 1, counting, None, kept=[top], first=[second])  # a product came back
    assert [c.listing_id for c in back.chosen] == [second.listing_id] and checked == [second.listing_id]
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `python -m pytest tests/test_picks_pipeline.py tests/test_picks_write.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scout.picks.pipeline'`, and the two new tests in `test_picks_write.py` fail.

- [x] **Step 3: Share the claim checks**

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


def attribute_text(entry: dict) -> str:
    text = f"{entry.get('value')} {entry.get('unit') or ''}".strip()
    return f"{text} (doubtful)" if entry.get("doubtful") else text
```

`attribute_text` shows a size that the worker doubts (Task 11). The answers of Task 7 use it too. Add `from typing import Sequence` to the imports. Then replace `check_picks` with:

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

- [x] **Step 4: Load cards by listing ID**

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

- [x] **Step 5: Write the pipeline**

Create `src/scout/picks/pipeline.py`:

```python
"""The picks steps, shared by `scout picks` and the chat agent."""
from dataclasses import dataclass, field
from typing import Callable, Collection, Sequence

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
    card_notes: dict[int, str] = field(default_factory=dict)  # listing_id -> the size notes of that pick

    def render(self) -> str:
        return format_picks(self.chosen, self.texts, self.notes, self.conditions_text, self.dropped, self.errors)


def shortlist(conn: psycopg.Connection, profile: CategoryProfile, needs: Needs, llm,
              excluded: Collection[int] = (), screened: dict[tuple[str, int], bool] | None = None) -> Shortlist:
    """SQL filter, implausible sizes, the user's conditions, the rank, and the title screening.

    `screened` keeps the screening results (wanted kind, listing_id) -> keep, so a title is screened one time.
    """
    cards = [c for c in load_cards(conn, profile.category, needs.budget_sek()) if c.product_id not in excluded]
    size_notes = drop_implausible_sizes(cards)
    cards, dropped = apply_conditions(cards, needs.rank_conditions())
    ranked = rank(cards)
    notes: list[str] = []
    if llm is None:
        notes.append("Titles were not screened, so items outside the category can appear.")
    elif ranked:
        wanted = needs.wanted(profile)
        screened = {} if screened is None else screened
        todo = [c for c in ranked if (wanted, c.listing_id) not in screened]
        try:
            if todo:
                kept = {c.listing_id for c in screen_titles(todo, profile, llm, wanted)}
                screened.update({(wanted, c.listing_id): c.listing_id in kept for c in todo})
            on_topic = [c for c in ranked if screened[(wanted, c.listing_id)]]
            if len(on_topic) < len(ranked):
                notes.append(f"{len(ranked) - len(on_topic)} stored items are not a "
                             f"'{wanted}' by their titles and were left out.")
            ranked = on_topic
        except (LLMUnavailable, LLMOutputError) as exc:
            notes.append(f"Title screening skipped: {exc}")
    return Shortlist(ranked, dropped, notes, size_notes)


def make_picks(short: Shortlist, needs: Needs, k: int, refetch: Refetch, llm, language: str = "English",
               kept: Sequence[Card] = (), first: Sequence[Card] = ()) -> PicksResult:
    """Recheck the top cards on the live pages, then write summaries with checked numbers.

    `kept` cards stay picks without a new check. `first` cards are checked before the shortlist. When kept and
    first are more than k, the last kept cards go.
    """
    kept = list(kept)[:max(0, k - len(first))]
    taken = {c.product_id for c in [*kept, *first]}
    ranked = [*first, *(c for c in short.ranked if c.product_id not in taken)]
    new, recheck_notes = recheck_picks(ranked, k - len(kept), refetch, needs.budget_sek(), needs.rank_conditions())
    chosen = [*kept, *new]
    card_notes = {c.listing_id: short.size_notes[c.listing_id] for c in chosen if c.listing_id in short.size_notes}
    notes = short.notes + recheck_notes
    texts, errors = None, []
    if chosen and llm is not None:
        try:
            texts, errors = write_picks(llm, chosen, needs.text(), language)
        except LLMUnavailable as exc:
            notes.append(f"No summaries: {exc}")
    notes += [card_notes[c.listing_id] for c in chosen if c.listing_id in card_notes]
    return PicksResult(chosen, texts, errors, notes, short.dropped, needs.text(), card_notes)


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

- [x] **Step 6: Make `scout picks` use the pipeline**

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

- [x] **Step 8: Commit**

```bash
git add src/scout/picks src/scout/llm/feedback.py src/scout/cli.py tests/test_picks_pipeline.py tests/test_picks_write.py
git commit -m "One picks pipeline for the command and the agent; shared claim checks; cards by listing ID"
```

---

### Task 4: Crawl a later result page, skip stored listings, record rejected products

**Files:**
- Modify: `src/scout/crawl.py`
- Modify: `src/scout/db/schema.sql`, `src/scout/db/store.py`
- Modify: `tests/conftest.py`, `tests/test_crawl.py`, `tests/test_store.py`

- [x] **Step 1: Write the failing tests**

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
from scout.db.store import add_eliminations, eliminated_products, known_source_ids, restore_products
from tests.test_picks_db import save


def test_rejected_products_belong_to_one_thread_and_can_come_back(db):
    save(db, "product_full.html", "B0AAAAAAA1")
    product_id = db.execute("SELECT id FROM products").fetchone()["id"]
    add_eliminations(db, "t1", [product_id], "too loud")
    add_eliminations(db, "t2", [product_id], "too big")
    assert eliminated_products(db, "t1") == {product_id}
    assert eliminated_products(db, "t3") == set()
    restore_products(db, "t1", [product_id])
    assert eliminated_products(db, "t1") == set() and eliminated_products(db, "t2") == {product_id}


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

- [x] **Step 3: Add the table and the store functions**

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


def restore_products(conn: psycopg.Connection, thread_id: str, product_ids: list[int]) -> None:
    conn.execute("DELETE FROM eliminations WHERE thread_id = %s AND product_id = ANY(%s)", (thread_id, product_ids))
    conn.commit()
```

Import `Candidate` from `scout.sources.base` in `store.py`, if it is not imported. Run `scout db-init` one time, so that the main database gets the new table.

- [x] **Step 4: Change the crawl**

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

- [x] **Step 5: Run all tests**

Run: `python -m pytest -q`
Expected: all tests pass.

- [x] **Step 6: Commit**

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

- [x] **Step 1: Write the failing tests**

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
    multiplied = {"messages": [user("budget 9k, or 1万, max 12 tusen; 5 kg, 60 kr")]}
    assert {Decimal("9000"), Decimal("10000"), Decimal("12000")} <= user_numbers(multiplied)
    assert Decimal("5000") not in user_numbers(multiplied) and Decimal("60000") not in user_numbers(multiplied)


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

- [x] **Step 3: Write the module**

Create `src/scout/agent/__init__.py`:

```python
"""The conversation agent: a LangGraph graph over the phase 1 steps."""
```

Create `src/scout/agent/history.py`:

```python
"""What the chat model sees of the conversation: a summary of the old turns and the recent messages."""
import re
from decimal import Decimal

from pydantic import BaseModel

from scout.picks.write import numbers_in
from scout.sources.amazon_se.numbers import to_decimal

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


# "9k", "9 tusen", "9千" and "1万" are written numbers too.
MULTIPLIED = re.compile(r"(\d+(?:[.,]\d+)?)\s*(k(?![a-zA-Z])|K(?![a-zA-Z])|tusen|thousand|千|万)")
MULTIPLIERS = {"k": 1000, "K": 1000, "tusen": 1000, "thousand": 1000, "千": 1000, "万": 10000}


def written_numbers(text: str) -> set[Decimal]:
    numbers = numbers_in(text)
    for raw, word in MULTIPLIED.findall(text):
        if (number := to_decimal(raw)) is not None:
            numbers.add(number * MULTIPLIERS[word])
    return numbers


def user_numbers(state: dict) -> set[Decimal]:
    """All numbers that the shopper wrote in this thread. Checks of later steps allow them."""
    numbers: set[Decimal] = set()
    for message in state.get("messages", []):
        if message["role"] == "user":
            numbers |= written_numbers(message["content"])
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

- [x] **Step 4: Run the tests**

Run: `python -m pytest tests/test_history.py -v`
Expected: 3 passed.

- [x] **Step 5: Commit**

```bash
git add src/scout/agent tests/test_history.py
git commit -m "Conversation history for the chat model: summary of old turns, recent messages, the user's numbers"
```

---

### Task 6: Clarify the needs

**Files:**
- Create: `src/scout/agent/clarify.py`
- Create: `tests/test_clarify.py`

- [x] **Step 1: Write the failing tests**

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
                        question=question)


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

- [x] **Step 3: Write the clarify step**

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
- Use only what the shopper said. Never guess a size or a budget.
- kind is the name of one kind in the profile, or null. Choose a kind when the words of the shopper describe it,
  also without its name: "a fridge for my wine bottles" is a wine cooler. When two kinds fit, ask.
- conditions use only number attributes of the profile, in the unit of the profile.
  Change units when necessary, for example 1,8 m is 180 cm. "At most" is <=, "at least" is >=.
- budget is in SEK, or null. If the shopper gives a budget in another currency, ask for SEK.
- preferences are short notes of soft wishes, for example "quiet" or "white".
- search_terms_sv is null. Only when the shopper asks for words that the kind does not cover, it is one to four
  Swedish words for the amazon.se search.
- ready is true when the kind is known and the shopper answered or skipped the questions of the profile.
  When the shopper asks you to search now, ready is true if the kind is known.
- When ready is false, write one short message in question. It can ask about two or three things.
  Write it in English, also when the shopper writes in another language."""

CATEGORY_SYSTEM = """You find which product category a shopper wants.
Return the name of one category from the list, or null when none fits or the shopper did not say."""


class ClarifyReply(BaseModel):
    needs: Needs
    ready: bool
    question: str = ""


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

- [x] **Step 4: Run the tests**

Run: `python -m pytest tests/test_clarify.py -v`
Expected: 4 passed.

- [x] **Step 5: Commit**

```bash
git add src/scout/agent/clarify.py tests/test_clarify.py
git commit -m "Clarify step: needs from the conversation, checked against the profile and the user's numbers"
```

---

### Task 7: Follow-up tools and checked replies

After the picks, the chat model acts through native tool calls. It can call several tools in one reply, and it can ask the shopper a question at any time. Python checks each call and each number before anything changes.

**Files:**
- Modify: `src/scout/llm/client.py` (tool calls), `tests/fakes.py` (`FakeLLM.tools`)
- Create: `src/scout/agent/followup.py`
- Create: `tests/test_llm_tools.py`, `tests/test_followup.py`

**The tools:**
- `remove_picks(picks, words, one_pick, reason)`: the model puts in `picks` every pick that fits the shopper's words, and copies these words into `words`. `one_pick` is true when the words name one pick ("that one", "it", "the Bomann"). When the words fit no pick, or several picks but the shopper means one, Python asks "Which pick do you mean" with the candidates, and nothing else of the turn runs. The model never has to choose by a guess.
- `restore_picks(product_ids)`: brings back products that the shopper removed in this thread. The prompt lists them.
- `next_step(step)`: one step that Python runs after the reply: `change_needs`, `show_more`, `recheck`, `new_search` or `calculate`.
- `reply(text, claims)`: the message to the shopper. Each number needs a claim, as in phase 1.

**The checks:** a pick number must exist. The words must be in the shopper's last message. A restored product must be in the removed list. Each tool can be called once. A reply sentence that says that products were removed or brought back needs the tool call; a question ("Which pick should be removed?") is allowed. A failed round goes back to the model with the problems (decision 8). After 3 failed rounds, nothing changes.

The model sees the full cards, not only the titles, so it can find "the widest one" or "the one without sizes". Python, not the model, writes what `remove_picks` and `restore_picks` did, so this text is always true.

Some models write the tool calls as JSON text in place of real tool calls. Nemotron 3 Ultra did this in 1 of 2 tries on 2026-10-05. The client reads these calls too.

- [x] **Step 1: Write the failing tests**

Create `tests/test_llm_tools.py`:

```python
import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from scout.llm.client import LLM, LLMOutputError, text_tool_calls, tool_name, tool_spec
from tests.test_llm import CONFIG, FakeClock


class RemovePicks(BaseModel):
    """Remove picks."""
    picks: list[int]


class Reply(BaseModel):
    """The message to the shopper."""
    text: str


def called(*calls):
    """A reply with real tool calls: (name, arguments) pairs."""
    tool_calls = [SimpleNamespace(function=SimpleNamespace(name=n, arguments=json.dumps(a))) for n, a in calls]
    return SimpleNamespace(content=None, tool_calls=tool_calls)


def written(text):
    """A reply with the tool calls as text."""
    return SimpleNamespace(content=text, tool_calls=None)


class FakeCompletions:
    def __init__(self, replies):
        self.replies, self.requests = list(replies), []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=self.replies.pop(0))],
                               usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5))


def make(replies):
    completions, clock = FakeCompletions(replies), FakeClock()
    fake = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return LLM(CONFIG, clients={"gemini": fake}, sleep=clock.sleep, clock=clock.clock), completions


def test_tool_calls_come_back_as_tool_objects_in_order():
    llm, completions = make([called(("remove_picks", {"picks": [2]}), ("reply", {"text": "Removed pick 2."}))])
    calls = llm.tools("chat", "sys", "user", [RemovePicks, Reply], name="Act")
    assert calls == [RemovePicks(picks=[2]), Reply(text="Removed pick 2.")]
    request = completions.requests[0]
    assert request["tool_choice"] == "required"
    assert [t["function"]["name"] for t in request["tools"]] == ["remove_picks", "reply"]
    assert [(c.schema, c.valid) for c in llm.calls] == [("Act", True)]


def test_tool_calls_written_as_text_are_accepted():
    text = '[[{"name": "remove_picks", "parameters": {"picks": [2]}}, {"name": "reply", "parameters": {"text": "ok"}}]]'
    llm, _ = make([written(text)])
    assert llm.tools("chat", "sys", "user", [RemovePicks, Reply]) == [RemovePicks(picks=[2]), Reply(text="ok")]


def test_a_wrong_call_is_sent_back_and_tried_again():
    llm, completions = make([called(("delete", {"picks": [2]})), written("I removed it."),
                             called(("remove_picks", {"picks": [2]}))])
    assert llm.tools("chat", "sys", "user", [RemovePicks, Reply]) == [RemovePicks(picks=[2])]
    assert "there is no tool delete" in completions.requests[1]["messages"][-1]["content"]
    assert "no tool call" in completions.requests[2]["messages"][-1]["content"]
    assert [c.valid for c in llm.calls] == [False, False, True]


def test_three_wrong_replies_raise():
    llm, _ = make([written("no")] * 3)
    with pytest.raises(LLMOutputError, match="no valid tool calls"):
        llm.tools("chat", "sys", "user", [Reply])


def test_tool_names_and_specs():
    assert tool_name(RemovePicks) == "remove_picks"
    spec = tool_spec(RemovePicks)["function"]
    assert spec["description"] == "Remove picks." and spec["parameters"]["required"] == ["picks"]
    assert "title" not in spec["parameters"]
    assert text_tool_calls("not json") == [] and text_tool_calls('{"text": "no name"}') == []
```

Create `tests/test_followup.py`:

```python
from decimal import Decimal

from scout.agent.followup import (AnswerClaim, NextStep, RemovePicks, Reply, RestorePicks, act, check_act,
                                  check_reply, says_done)
from tests.fakes import FakeLLM
from tests.test_picks_rank import card

PICKS = [{"listing_id": 1, "product_id": 1, "title": "Bomann KG 320"},
         {"listing_id": 2, "product_id": 2, "title": "Bomann SBS 7344"},
         {"listing_id": 3, "product_id": 3, "title": "FOHERE 90L"}]
CARDS = [card(1, 4.4, 1779, height=143.0, no_frost=True), card(2, 4.2, 54, height=177.0),
         card(3, 4.2, 54, height=48.0)]
REMOVED = [{"product_id": 9, "listing_id": 9, "title": "Severin RKG 8930"}]


def run_act(calls, last_user, removed=()):
    llm = FakeLLM(calls)
    return act(llm, PICKS, CARDS, list(removed), f"Shopper: {last_user}", set(), last_user), llm


def test_check_reply_allows_claims_the_shoppers_numbers_and_pick_numbers():
    good = Reply(text="Pick 1 is 143 cm high, under your 186.",
                 claims=[AnswerClaim(pick=1, field="height", value="143")])
    assert check_reply(good, CARDS, {Decimal("186")}) == []
    bad = Reply(text="Pick 3 is 80 cm high.", claims=[AnswerClaim(pick=3, field="height", value="80")])
    errors = check_reply(bad, CARDS, set())
    assert any("height is 48.0, not 80" in e for e in errors) and any("the number 80" in e for e in errors)
    assert check_reply(Reply(text="ok", claims=[AnswerClaim(pick=4, field="price", value="1")]), CARDS, set()) == [
        "there is no pick 4"]


def test_a_clear_removal_and_a_next_step_in_one_reply():
    result, llm = run_act([[RemovePicks(picks=[3], words="the FOHERE", one_pick=True, reason="too small"),
                            NextStep(step="show_more")]], "Drop the FOHERE and show me more")
    assert result.remove.picks == [3] and result.step == "show_more" and result.action == "more"
    assert result.question is None and result.tools == ["remove_picks", "next_step"]
    assert "Pick 3: Fridge 3 | price=5000 SEK" in llm.requests[0][1] and "height=48.0" in llm.requests[0][1]
    assert "product 9" not in llm.requests[0][1]


def test_one_pick_meant_but_several_fit_makes_python_ask():
    result, _ = run_act([[RemovePicks(picks=[2, 1], words="the Bomann", one_pick=True),
                          Reply(text="Removed the Bomann.")]], "Remove the Bomann")
    assert result.action == "stop"
    assert result.question == ("Which pick do you mean: pick 1 (Bomann KG 320), pick 2 (Bomann SBS 7344)? "
                               "Give its number.")
    result, _ = run_act([[RemovePicks(picks=[], words="that one", one_pick=True)]], "Remove that one")
    assert result.question.startswith("Which pick do you want to remove?")


def test_several_picks_can_go_when_the_shopper_means_several():
    result, _ = run_act([[RemovePicks(picks=[1, 2], words="both Bomanns", one_pick=False)]], "Remove both Bomanns")
    assert result.remove.picks == [1, 2] and result.question is None and result.action == "exclude"


def test_a_reply_that_claims_a_removal_without_the_tool_goes_back():
    errors = check_act([Reply(text="Removed pick 3.")], CARDS, set(), set(), "remove that one")
    assert any("you did not call remove_picks" in e for e in errors)
    assert check_act([Reply(text="Which pick should be removed?")], CARDS, set(), set(), "remove it") == []
    assert says_done("Done. I removed pick 2.") and not says_done("Do you want pick 2 removed?")


def test_words_must_come_from_the_shopper_and_picks_must_exist():
    errors = check_act([RemovePicks(picks=[4], words="the loud one", one_pick=True)], CARDS, set(), set(),
                       "Remove the noisy one")
    assert "there is no pick 4" in errors
    assert any("the words 'the loud one' are not in the shopper's last message" in e for e in errors)
    assert check_act([RemovePicks(picks=[2], words="第二个", one_pick=True)], CARDS, set(), set(), "第二个我不要") == []
    assert check_act([NextStep(step="recheck"), NextStep(step="show_more")], CARDS, set(), set(), "x") == [
        "call next_step only once"]


def test_restore_takes_only_removed_products():
    assert check_act([RestorePicks(product_ids=[5])], CARDS, {9}, set(), "bring it back") == [
        "product 5 was not removed in this conversation"]
    result, llm = run_act([[RestorePicks(product_ids=[9])]], "Bring back the Severin", REMOVED)
    assert result.action == "restore" and "product 9: Severin RKG 8930" in llm.requests[0][1]


def test_act_retries_with_the_problems_and_gives_up_after_three_rounds():
    bad = [Reply(text="Pick 3 is 80 cm high.")]
    good = [Reply(text="Pick 3 is 48 cm high.", claims=[AnswerClaim(pick=3, field="height", value="48")])]
    result, llm = run_act([bad, good], "How tall is 3?")
    assert result.reply == good[0] and result.action == "answer"
    assert "the number 80" in llm.requests[1][1] and '"tool":"reply"' in llm.requests[1][1]
    assert [c.schema for c in llm.calls] == ["Act", "Act"]
    assert run_act([bad] * 3, "How tall?")[0] is None
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `python -m pytest tests/test_llm_tools.py tests/test_followup.py -v`
Expected: FAIL with `ImportError` (no `text_tool_calls`) and `ModuleNotFoundError`.

- [x] **Step 3: Add tool calls to the LLM client**

In `src/scout/llm/client.py`, add `import re` after `import os`. Change the first line of the file to `"""One calling interface for OpenAI-compatible endpoints (Gemini, Groq) with structured output and tool calls."""`. Add these functions before `strip_fences`:

```python
def tool_name(tool: type[BaseModel]) -> str:
    """RemovePicks -> remove_picks"""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", tool.__name__).lower()


def tool_spec(tool: type[BaseModel]) -> dict:
    parameters = inline_refs(tool.model_json_schema())
    parameters.pop("title", None)
    parameters.pop("description", None)
    return {"type": "function", "function": {"name": tool_name(tool), "description": (tool.__doc__ or "").strip(),
                                             "parameters": parameters}}


def text_tool_calls(text: str) -> list[tuple[str, str]]:
    """Tool calls that a model wrote as JSON text in place of real tool calls. Nemotron does this at times."""
    try:
        data = json.loads(strip_fences(text))
    except ValueError:
        return []
    items, calls = [data], []
    while items:
        item = items.pop(0)
        if isinstance(item, list):
            items = item + items
        elif isinstance(item, dict) and isinstance(item.get("name"), str):
            args = item.get("arguments", item.get("parameters", {}))
            calls.append((item["name"], args if isinstance(args, str) else json.dumps(args)))
    return calls
```

Replace the method `structured` with these three methods. The fallback logic does not change. It moves to `_with_fallback`, so that tool calls use it too:

```python
    def structured(self, role: Role, system: str, user: str, schema: type[T]) -> T:
        return self._with_fallback(role, lambda cfg: self._structured(role, cfg, system, user, schema))

    def tools(self, role: Role, system: str, user: str, tools: list[type[BaseModel]], name: str = "tools") -> list:
        """The model calls one or more of the tools. Return the arguments of each call, as tool objects, in order."""
        return self._with_fallback(role, lambda cfg: self._tool_calls(role, cfg, system, user, tools, name))

    def _with_fallback(self, role: Role, call):
        fallback = self.config.roles[role].fallback
        if role not in self.fallbacks:
            try:
                return call(self.config.roles[role])
            except LLMUnavailable as exc:
                if fallback is None:
                    raise
                key_env = self.config.providers[fallback.provider].api_key_env
                if fallback.provider not in self._clients and not os.environ.get(key_env):
                    raise LLMUnavailable(f"{exc} The fallback {fallback.model} needs {key_env} in .env.") from exc
                self.fallbacks[role] = str(exc)
        return call(fallback)
```

Add this method at the end of `LLM`:

```python
    def _tool_calls(self, role: Role, role_cfg: ModelConfig, system: str, user: str,
                    tools: list[type[BaseModel]], name: str) -> list:
        client = self.client(role_cfg.provider)
        limiter = self._limiters.setdefault(
            (role_cfg.provider, role_cfg.model), RateLimiter(role_cfg.rpm, self._sleep, self._clock))
        by_name = {tool_name(t): t for t in tools}
        specs = [tool_spec(t) for t in tools]
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        last_error = ""
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            limiter.wait()
            started = self._clock()
            try:
                with self._tracer.generation(
                    f"{role} {name}", role_cfg.model, input=messages,
                    metadata={"provider": role_cfg.provider, "attempt": attempt},
                ) as gen:
                    response = client.chat.completions.create(
                        model=role_cfg.model, messages=messages, tools=specs, tool_choice="required"
                    )
                    message = response.choices[0].message
                    raw = [(call.function.name, call.function.arguments) for call in (message.tool_calls or [])]
                    raw = raw or text_tool_calls(message.content or "")
                    usage = getattr(response, "usage", None)
                    tokens_in = getattr(usage, "prompt_tokens", 0) or 0
                    tokens_out = getattr(usage, "completion_tokens", 0) or 0
                    gen.update(output=raw or message.content, usage_details={"input": tokens_in, "output": tokens_out})
            except RateLimitError as exc:
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
            record = CallRecord(role, role_cfg.model, tokens_in, tokens_out, self._clock() - started, name)
            self.calls.append(record)
            problems, calls = [], []
            for tool, args in raw:
                if tool not in by_name:
                    problems.append(f"there is no tool {tool}")
                    continue
                try:
                    calls.append(by_name[tool].model_validate_json(args or "{}"))
                except ValidationError as exc:
                    problems.append(f"{tool}: {exc.errors()[:3]}")
            if calls and not problems:
                return calls
            record.valid = False
            last_error = "; ".join(problems) or "no tool call"
            messages = messages + [{"role": "user", "content": f"Your reply did not call the tools correctly "
                                                               f"({last_error}). Call the tools again."}]
        if last_error == "rate limited":
            raise QuotaExhausted(f"{role_cfg.model} is still rate limited after {self.MAX_ATTEMPTS} tries.")
        if last_error == "unavailable":
            raise LLMUnavailable(f"{role_cfg.model} is busy or unreachable after {self.MAX_ATTEMPTS} tries.")
        raise LLMOutputError(f"{role_cfg.model} gave no valid tool calls after {self.MAX_ATTEMPTS} tries: {last_error}")
```

`tool_choice="required"` makes the model call at least one tool. On 2026-10-05, I sent one message that needs two tools (`remove_picks` and `reply`) to each provider. Groq `openai/gpt-oss-120b`, Gemini `gemini-3.5-flash-lite` and Mistral `ministral-14b-latest` returned real tool calls. NVIDIA `nvidia/nemotron-3-ultra-550b-a55b` returned real tool calls in 1 of 2 tries, and the same calls as JSON text in the other. `gemini-3.8-flash` was busy (503) at that time.

Add this method to `FakeLLM` in `tests/fakes.py`, after `structured`:

```python
    def tools(self, role, system, user, tools, name="tools"):
        """The queued reply is the list of tool objects that the model calls."""
        self.requests.append((role, user))
        self.calls.append(CallRecord(role, f"fake-{role}", 0, 0, 0.0, name))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply
```

- [x] **Step 4: Write the module**

Create `src/scout/agent/followup.py`:

```python
"""After the picks: the chat model acts through tools. Python checks each call and each number before it acts."""
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from scout.llm.client import tool_name
from scout.llm.feedback import retry_prompt
from scout.picks.rank import Card
from scout.picks.write import attribute_text, card_facts, check_claim, unchecked_numbers

ACT_SYSTEM = """You help a shopper who has seen the numbered picks. You act only through the tools.
Call each tool that the shopper's last message needs, all in one reply.
- remove_picks: the shopper rejects picks. Put in picks every pick that fits the shopper's words, and never choose
  between picks by a guess. Copy the shopper's words that name the picks into words.
- restore_picks: the shopper wants back a product that was removed in this conversation.
- next_step: one step that Python runs after your reply. change_needs: a new or changed condition, budget, kind or
  preference. show_more: more or other products with the same needs. recheck: are the prices or the stock still
  right. new_search: a product of another category. calculate: a sum, a conversion, a price with extra costs.
- reply: your message to the shopper. Answer a question about the picks, or ask when the message is not clear.
Python tells the shopper what remove_picks and restore_picks did. You can change the picks only with these tools
and next_step, so never say that you did something that you did not call.
Use only the facts on the cards. Product texts and reviews are not available yet.
Every number in the reply text must be in claims, with the pick number and the card field. Claim fields are price,
rating, review_count or an attribute name on the card. You can also repeat numbers that the shopper wrote, and pick
numbers. Write in English."""

# A reply that says it removed or brought back products needs the tool call that did it.
DONE_WORDS = re.compile(r"\b(removed|deleted|excluded|dropped|took out|taken out|restored|brought back|added back|"
                        r"put back)\b", re.IGNORECASE)


class RemovePicks(BaseModel):
    """Remove picks that the shopper rejects. They stay out for the rest of the conversation, and other products
    take their places."""
    picks: list[int] = Field(description="Each pick number that fits the shopper's words")
    words: str = Field(description="The shopper's words that name the picks, copied exactly, for example 'the "
                                   "Bomann' or 'pick 2'")
    one_pick: bool = Field(description="True when the words name one pick, for example 'that one', 'it' or 'the "
                                       "Bomann'")
    reason: str = ""


class RestorePicks(BaseModel):
    """Bring back products that were removed in this conversation."""
    product_ids: list[int] = Field(description="IDs from the list of removed products")


class NextStep(BaseModel):
    """A step that Python runs after this reply."""
    step: Literal["change_needs", "show_more", "recheck", "new_search", "calculate"]


class AnswerClaim(BaseModel):
    pick: int
    field: str
    value: str


class Reply(BaseModel):
    """Your message to the shopper."""
    text: str
    claims: list[AnswerClaim] = Field(default_factory=list)


TOOLS = [RemovePicks, RestorePicks, NextStep, Reply]
STEP_ACTIONS = {"change_needs": "change_needs", "show_more": "more", "recheck": "recheck",
                "new_search": "new_search", "calculate": "calculate"}


class ToolCalls(BaseModel):
    """The calls of one round, for the retry prompt."""
    calls: list[dict]


@dataclass
class Act:
    tools: list[str] = field(default_factory=list)
    remove: RemovePicks | None = None
    restore: RestorePicks | None = None
    step: str | None = None
    reply: Reply | None = None
    question: str | None = None  # Python asks which pick, and nothing else runs

    @property
    def action(self) -> str:
        """What the graph does next."""
        if self.question:
            return "stop"
        if self.step:
            return STEP_ACTIONS[self.step]
        if self.remove:
            return "exclude"
        if self.restore:
            return "restore"
        return "answer"


def card_text(n: int, card: Card) -> str:
    attrs = ", ".join(f"{k}={attribute_text(v)}" for k, v in card.attributes.items())
    return (f"Pick {n}: {card.title} | price={card.price} {card.currency or ''} | rating={card.rating} | "
            f"review_count={card.review_count} | {attrs or 'no attributes on the card'}")


def check_reply(reply: Reply, cards: list[Card], allowed: set[Decimal]) -> list[str]:
    errors: list[str] = []
    claimed: set[Decimal] = set()
    for claim in reply.claims:
        if not 1 <= claim.pick <= len(cards):
            errors.append(f"there is no pick {claim.pick}")
            continue
        error, number = check_claim(card_facts(cards[claim.pick - 1]), claim.field, claim.value)
        if error:
            errors.append(f"pick {claim.pick}: {error}")
        elif number is not None:
            claimed.add(number)
    pick_numbers = {Decimal(n) for n in range(1, len(cards) + 1)}
    for raw in unchecked_numbers(reply.text, claimed | allowed | pick_numbers, [c.model for c in cards]):
        errors.append(f"the number {raw} in text is not in the claims. Add a claim with the pick and the field "
                      "that has it, or remove it")
    return errors


def says_done(text: str) -> bool:
    """True when a sentence that is not a question says that products were removed or brought back."""
    return any(DONE_WORDS.search(s) for s in re.findall(r"[^.!?\n]+[.!?]?", text) if not s.strip().endswith("?"))


def plain(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text.casefold()).split())


def check_act(calls: list, cards: list[Card], removed_ids: set[int], allowed: set[Decimal],
              last_user: str) -> list[str]:
    """The problems in one round of tool calls. An empty list means that Python can act."""
    errors: list[str] = []
    names = [tool_name(type(c)) for c in calls]
    errors += [f"call {n} only once" for n in sorted(set(names)) if names.count(n) > 1]
    did = set()
    for call in calls:
        if isinstance(call, RemovePicks):
            errors += [f"there is no pick {n}" for n in call.picks if not 1 <= n <= len(cards)]
            if plain(call.words) not in plain(last_user):
                errors.append(f"the words '{call.words}' are not in the shopper's last message. Copy the words "
                              "that name the picks")
            did.add("remove")
        elif isinstance(call, RestorePicks):
            errors += [f"product {i} was not removed in this conversation" for i in call.product_ids
                       if i not in removed_ids]
            did.add("restore")
    for call in calls:
        if isinstance(call, Reply):
            errors += check_reply(call, cards, allowed)
            if says_done(call.text) and not did:
                errors.append("the reply says that products were removed or brought back, but you did not call "
                              "remove_picks or restore_picks. Call the tool, or change the text")
    return errors


def unclear_question(remove: RemovePicks, picks: list[dict]) -> str | None:
    """Python asks when the words fit no pick, or several picks but the shopper means one."""
    if not remove.picks:
        return "Which pick do you want to remove? Give its number."
    if remove.one_pick and len(remove.picks) > 1:
        options = ", ".join(f"pick {n} ({picks[n - 1]['title'][:50]})" for n in sorted(remove.picks))
        return f"Which pick do you mean: {options}? Give its number."
    return None


def collect(calls: list, picks: list[dict]) -> Act:
    act = Act(tools=[tool_name(type(c)) for c in calls])
    for call in calls:
        if isinstance(call, RemovePicks):
            act.remove = call
        elif isinstance(call, RestorePicks):
            act.restore = call
        elif isinstance(call, NextStep):
            act.step = call.step
        elif isinstance(call, Reply):
            act.reply = call
    if act.remove:
        act.remove.picks = sorted(set(act.remove.picks))
        act.question = unclear_question(act.remove, picks)
    return act


def act(llm, picks: list[dict], cards: list[Card], removed: list[dict], history: str, allowed: set[Decimal],
        last_user: str, max_rounds: int = 3) -> Act | None:
    """Return the checked tool calls, or None when no round passed the checks."""
    gone = "\n".join(f"product {r['product_id']}: {r['title']}" for r in removed) or "none"
    base = ("Cards:\n" + "\n".join(card_text(n, c) for n, c in enumerate(cards, 1))
            + f"\n\nRemoved in this conversation:\n{gone}\n\nConversation:\n{history}")
    last, errors = None, []
    for _ in range(max_rounds):
        calls = llm.tools("chat", ACT_SYSTEM, retry_prompt(base, last, errors), TOOLS, name="Act")
        errors = check_act(calls, cards, {r["product_id"] for r in removed}, allowed, last_user)
        if not errors:
            return collect(calls, picks)
        last = ToolCalls(calls=[{"tool": tool_name(type(c)), "args": c.model_dump()} for c in calls])
    return None
```

- [x] **Step 5: Run the tests**

Run: `python -m pytest tests/test_llm_tools.py tests/test_followup.py -v`
Expected: 13 passed.

- [x] **Step 6: Commit**

```bash
git add src/scout/llm/client.py tests/fakes.py src/scout/agent/followup.py tests/test_llm_tools.py tests/test_followup.py
git commit -m "Follow-up tools: native tool calls, checked replies, Python asks when a pick is not clear"
```

---

### Task 8: The calculation tool

The chat model writes the steps. Python does the arithmetic and shows the formula. Each number in a step must be a fact on a pick, a number that the user wrote, an earlier step, or the constant 100 for percentages. A plan that fails a check goes back to the model with the problem (decision 8). In a real run on 2026-10-05, the model put a whole formula into one number field, so the check needs this.

**Files:**
- Create: `src/scout/agent/calc.py`
- Create: `tests/test_calc.py`

- [x] **Step 1: Write the failing tests**

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
    with pytest.raises(CalcError, match="11.5 is not a number that the shopper wrote"):
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


def test_a_formula_in_number_goes_back_to_the_model():
    formula = plan(("total", "add", Operand(number="(pick 1 price) + 499 = 5499"), Operand(number="499")))
    good = plan(("total", "add", PRICE, Operand(number="499")))
    llm = FakeLLM([formula, good])
    text = calculate(llm, CARDS, "Shopper: plus 499 for shipping", {Decimal("499")})
    assert "total = 5000 (pick 1 price) + 499 = 5499" in text
    assert "is not one plain number" in llm.requests[1][1] and "Your last answer" in llm.requests[1][1]
    assert "could not turn this request" in calculate(FakeLLM([formula] * 3), CARDS, "Shopper: plus 499",
                                                      {Decimal("499")})
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `python -m pytest tests/test_calc.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [x] **Step 3: Write the tool**

Create `src/scout/agent/calc.py`:

```python
"""The calculation tool. The chat model writes the steps. Python does the arithmetic and shows the formula."""
import re
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from pydantic import BaseModel, Field

from scout.agent.followup import card_text
from scout.llm.feedback import retry_prompt
from scout.picks.rank import Card
from scout.picks.write import card_facts, unchecked_numbers
from scout.sources.amazon_se.numbers import to_decimal

SYSTEM = """You change a shopper's calculation request into steps. You never calculate yourself.
Each step has a name, an operation (add, subtract, multiply or divide) and two operands, a and b.
An operand is one of these:
- one number that the shopper wrote, copied into number, for example "499". Never a formula or a unit.
- a fact of a pick, written into ref as "pick N field", for example "pick 1 price"
- the name of an earlier step, written into ref
For a percentage, divide by 100 in a step. result is the name of the step with the answer.
explanation is one short sentence in English that says what the result is. It has no numbers.
If the request needs a number that the shopper did not give, such as an exchange rate,
return no steps and ask for that number in explanation."""

CONSTANTS = {Decimal("100")}  # for percentages
PLAIN_NUMBER = re.compile(r"-?\d[\d\u00a0\u202f ]*(?:[.,]\d+)?")
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
            number = to_decimal(op.number) if PLAIN_NUMBER.fullmatch(op.number.strip()) else None
            if number is None:
                raise CalcError(f"number '{op.number}' is not one plain number. Put a fact of a pick into ref, "
                                "and a number of the shopper into number")
            if number not in said:
                raise CalcError(f"{op.number} is not a number that the shopper wrote")
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


def calculate(llm, cards: list[Card], history: str, said: set[Decimal], max_rounds: int = 3) -> str:
    base = "Cards:\n" + "\n".join(card_text(n, c) for n, c in enumerate(cards, 1)) + f"\n\nConversation:\n{history}"
    plan, errors = None, []
    for _ in range(max_rounds):
        plan = llm.structured("chat", SYSTEM, retry_prompt(base, plan, errors), CalcPlan)
        if not plan.steps:
            return plan.explanation
        try:
            _, lines = run_plan(plan, cards, said | CONSTANTS)
        except CalcError as exc:
            errors = [str(exc)]
            continue
        allowed = said | {Decimal(n) for n in range(1, len(cards) + 1)}
        explanation = plan.explanation if not unchecked_numbers(plan.explanation, allowed) else "Result:"
        return "\n".join([explanation, *lines, "Python did the arithmetic. Results are rounded to 2 decimals."])
    return (f"I could not turn this request into steps that Python can check ({errors[0]}). "
            "You can write the calculation in other words.")
```

- [x] **Step 4: Run the tests**

Run: `python -m pytest tests/test_calc.py -v`
Expected: 6 passed.

- [x] **Step 5: Commit**

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
                        └── yes ──→ act (tool calls) ─┬─ reply only ─────────→ END
                                                      ├─ pick not clear ─────→ "Which pick do you mean" → END
                                                      ├─ calculate ──────────→ END
                                                      ├─ remove_picks ───────→ recommend (keeps the other picks) → END
                                                      ├─ restore_picks ──────→ recommend (the products that came back first)
                                                      ├─ change_needs ───────→ clarify
                                                      ├─ show_more ──────────→ search (always crawls) → recommend (new products only)
                                                      ├─ recheck ────────────→ recommend (the shown picks first)
                                                      └─ new_search ─────────→ reset → clarify
```

`act` runs the checked tool calls of Task 7 in this order: `remove_picks`, `restore_picks`, `reply`. Then the graph goes to the `next_step`. Without a `next_step`, a removal or a restore goes to `recommend`, and a reply alone ends the turn. So "Drop the FOHERE and show me more" removes pick 4 and then searches for more. The state keeps the removed products with their titles, so the model can bring them back.

Each node except `compact` catches all errors. The node then gives a short message and the turn ends: a failed search does not go on to `recommend`. The state stays as it was, so the user can try again.

The search reads result page 1 again when the budget is higher than the lowest budget of the earlier reads of these words. `searches` keeps the last page and that budget for each set of search words. `seen` keeps the products that the thread showed since the needs last changed. Each pick in the state keeps its doubtful sizes and its note, so a kept pick and an answer show them without a new page load.

- [x] **Step 1: Write the failing tests**

Create `tests/test_graph.py`:

```python
import copy

from langgraph.checkpoint.memory import InMemorySaver

from scout.agent.clarify import CategoryChoice, ClarifyReply
from scout.agent.followup import AnswerClaim, NextStep, RemovePicks, Reply, RestorePicks
from scout.agent.graph import Deps, build_graph
from scout.crawl import CrawlReport
from scout.llm.client import QuotaExhausted
from scout.needs import Needs
from scout.picks.pipeline import PicksResult
from scout.profiles import load_profile
from tests.fakes import FakeLLM
from tests.test_picks_rank import card

FRIDGE = load_profile("fridge")
TERMS = "kyl och frys"


class World:
    """A fake shop and database behind the graph. It shows k picks out of the stored cards."""

    def __init__(self, stored=20, n=4, k=2, doubts=None):
        self.stored, self.k, self.doubts = stored, k, doubts or {}
        self.crawls, self.removed, self.said, self.picked = [], {}, [], []
        heights = [143.0, 170.0, 150.0, 160.0]
        self.cards = {i: card(i, 4.5, 100, height=heights[i - 1]) for i in range(1, n + 1)}

    def deps(self, llm):
        return Deps(
            llm=llm,
            profile_names=lambda: ["fridge"],
            load_profile=lambda name: FRIDGE,
            count=lambda profile, needs, excluded: self.stored,
            crawl=self.crawl,
            picks=self.picks,
            cards=lambda ids: [copy.deepcopy(self.cards[i]) for i in ids],  # fresh from the database
            eliminate=lambda thread, ids, reason: self.removed.setdefault(thread, set()).update(ids),
            eliminated=lambda thread: self.removed.get(thread, set()),
            restore=lambda thread, ids: self.removed.setdefault(thread, set()).difference_update(ids),
            say=self.said.append,
        )

    def crawl(self, profile, needs, page):
        self.crawls.append((needs.search_terms(profile), page))
        return CrawlReport(candidates=48, saved=20)

    def picks(self, profile, needs, excluded, kept, first):
        self.picked.append((set(excluded), [c.listing_id for c in kept], [c.listing_id for c in first]))
        kept = kept[:max(0, self.k - len(first))]
        taken = set(excluded) | {c.product_id for c in [*kept, *first]}
        others = [copy.deepcopy(c) for c in self.cards.values() if c.product_id not in taken]
        new = [*first, *others][:self.k - len(kept)]
        notes = {}
        for c in new:
            if c.listing_id in self.doubts:
                c.attributes["height"] = {**c.attributes["height"], "doubtful": True}
                notes[c.listing_id] = self.doubts[c.listing_id]
        return PicksResult([*kept, *new], None, [], list(notes.values()), 0, needs.text(), notes)


def run(graph, thread, text):
    return graph.invoke({"messages": [{"role": "user", "content": text}]}, {"configurable": {"thread_id": thread}})


def ready(**fields):
    return ClarifyReply(needs=Needs(kind="fridge_freezer", **fields), ready=True)


def shown(state):
    return [p["listing_id"] for p in state["picks"]]


def test_clarify_asks_first_and_does_not_search():
    world = World()
    llm = FakeLLM([CategoryChoice(category="fridge"), ClarifyReply(needs=Needs(), ready=False, question="Which kind?")])
    state = run(build_graph(world.deps(llm), InMemorySaver()), "t1", "I want a fridge")
    assert state["messages"][-1]["content"] == "Which kind?"
    assert state["phase"] == "clarify" and world.crawls == []


def test_a_new_thread_reads_page_1_once_then_uses_the_stored_products():
    world = World(stored=20)
    llm = FakeLLM([CategoryChoice(category="fridge"), ready(budget=9000), [NextStep(step="change_needs")],
                   ready(budget=8000)])
    graph = build_graph(world.deps(llm), InMemorySaver())
    state = run(graph, "t1", "A fridge with a freezer, budget 9000")
    assert world.crawls == [(TERMS, 1)] and state["searches"] == {TERMS: {"page": 1, "budget": 9000}}
    assert state["phase"] == "picks" and shown(state) == [1, 2]
    assert state["messages"][-1]["content"].startswith("Conditions: budget 9000 SEK")
    state = run(graph, "t1", "Make the budget 8000")
    assert world.crawls == [(TERMS, 1)]
    assert "enough stored products match" in state["messages"][-2]["content"]


def test_a_higher_budget_reads_page_1_again():
    world = World(stored=20)
    llm = FakeLLM([CategoryChoice(category="fridge"), ready(budget=9000), [NextStep(step="change_needs")],
                   ready(budget=12000), [NextStep(step="change_needs")], ready(budget=10000)])
    graph = build_graph(world.deps(llm), InMemorySaver())
    run(graph, "t1", "A fridge with a freezer, budget 9000")
    state = run(graph, "t1", "Make the budget 12000")
    assert world.crawls == [(TERMS, 1), (TERMS, 1)] and state["searches"][TERMS] == {"page": 1, "budget": 12000}
    assert "the budget is higher than in the earlier search" in world.said[-1]
    run(graph, "t1", "Make the budget 10000")
    assert len(world.crawls) == 2


def test_few_stored_products_start_a_search_with_the_kind_terms():
    world = World(stored=3)
    llm = FakeLLM([CategoryChoice(category="fridge"), ready()])
    state = run(build_graph(world.deps(llm), InMemorySaver()), "t1", "A fridge with a freezer")
    assert world.crawls == [(TERMS, 1)] and state["searches"] == {TERMS: {"page": 1, "budget": None}}
    assert f"Searched amazon.se for '{TERMS}'" in state["messages"][-2]["content"]
    assert world.said and TERMS in world.said[0]


def test_follow_ups_in_one_thread():
    world = World()
    llm = FakeLLM([CategoryChoice(category="fridge"), ready(),
                   [RemovePicks(picks=[1], words="the first one", one_pick=True, reason="too small")],
                   [Reply(text="Pick 1 is 170 cm high.", claims=[AnswerClaim(pick=1, field="height", value="170")])],
                   [NextStep(step="show_more")]])
    graph = build_graph(world.deps(llm), InMemorySaver())
    run(graph, "t1", "A fridge with a freezer")
    state = run(graph, "t1", "Remove the first one, it is too small")
    assert world.removed["t1"] == {1} and shown(state) == [2, 3]
    assert world.picked[-1] == ({1}, [2], [])  # pick 2 stays without a new check
    assert state["removed"] == [{"product_id": 1, "listing_id": 1, "title": "Fridge 1"}]
    state = run(graph, "t1", "How tall is it?")
    assert state["messages"][-1]["content"] == "Pick 1 is 170 cm high."
    state = run(graph, "t1", "Show me more")
    assert world.crawls == [(TERMS, 1), (TERMS, 2)] and state["phase"] == "picks"
    assert world.picked[-1] == ({1, 2, 3}, [], []) and shown(state) == [4]


def test_more_keeps_the_picks_when_no_other_product_matches():
    world = World(n=2)
    llm = FakeLLM([CategoryChoice(category="fridge"), ready(), [NextStep(step="show_more")]])
    graph = build_graph(world.deps(llm), InMemorySaver())
    run(graph, "t1", "A fridge with a freezer")
    state = run(graph, "t1", "Show me others")
    assert "No other stored product" in state["messages"][-1]["content"] and shown(state) == [1, 2]


def test_recheck_checks_the_shown_picks_first():
    world = World()
    llm = FakeLLM([CategoryChoice(category="fridge"), ready(), [NextStep(step="recheck")]])
    graph = build_graph(world.deps(llm), InMemorySaver())
    run(graph, "t1", "A fridge with a freezer")
    run(graph, "t1", "Are the prices still right?")
    assert world.picked[-1] == (set(), [], [1, 2])


def test_a_kept_pick_keeps_its_size_doubt():
    world = World(doubts={2: "Fridge 2: height may be wrong."})
    llm = FakeLLM([CategoryChoice(category="fridge"), ready(),
                   [RemovePicks(picks=[1], words="Remove 1", one_pick=True)], [Reply(text="See the picks.")]])
    graph = build_graph(world.deps(llm), InMemorySaver())
    run(graph, "t1", "A fridge with a freezer")
    state = run(graph, "t1", "Remove 1")
    assert state["picks"][0]["doubtful"] == ["height"] and state["picks"][0]["note"] == "Fridge 2: height may be wrong."
    assert "Fridge 2: height may be wrong." in state["messages"][-1]["content"]
    run(graph, "t1", "How tall is pick 1?")
    assert "height=170.0 (doubtful)" in llm.requests[-1][1]


def test_an_unclear_removal_asks_which_pick_and_removes_nothing():
    world = World()
    llm = FakeLLM([CategoryChoice(category="fridge"), ready(),
                   [RemovePicks(picks=[1, 2], words="that one", one_pick=True), Reply(text="Removed it.")]])
    graph = build_graph(world.deps(llm), InMemorySaver())
    run(graph, "t1", "A fridge with a freezer")
    state = run(graph, "t1", "Remove that one")
    assert state["messages"][-1]["content"].startswith("Which pick do you mean: pick 1 (Fridge 1), pick 2")
    assert world.removed == {} and shown(state) == [1, 2] and len(world.picked) == 1


def test_a_removed_product_can_come_back():
    world = World()
    llm = FakeLLM([CategoryChoice(category="fridge"), ready(),
                   [RemovePicks(picks=[1], words="pick 1", one_pick=True)],
                   [RestorePicks(product_ids=[1]), Reply(text="Pick 1 is back, as you asked.")]])
    graph = build_graph(world.deps(llm), InMemorySaver())
    run(graph, "t1", "A fridge with a freezer")
    run(graph, "t1", "Remove pick 1")
    state = run(graph, "t1", "No, I want it back")
    assert world.removed["t1"] == set() and state["removed"] == []
    assert world.picked[-1] == (set(), [2, 3], [1]) and shown(state) == [2, 1]
    texts = [m["content"] for m in state["messages"][-3:]]
    assert texts[0] == "Brought back: Fridge 1." and texts[1] == "Pick 1 is back, as you asked."


def test_a_reply_that_fails_three_rounds_changes_nothing():
    world = World()
    bad = [Reply(text="Removed pick 1, it is 80 cm high.")]
    llm = FakeLLM([CategoryChoice(category="fridge"), ready(), bad, bad, bad])
    graph = build_graph(world.deps(llm), InMemorySaver())
    run(graph, "t1", "A fridge with a freezer")
    state = run(graph, "t1", "Remove the small one")
    assert "so I changed nothing" in state["messages"][-1]["content"] and world.removed == {}
    assert "you did not call remove_picks" in llm.requests[-1][1]


def test_an_unavailable_model_gives_a_message_and_the_turn_ends():
    world = World()
    llm = FakeLLM([QuotaExhausted("The free daily quota is used up.")])
    state = run(build_graph(world.deps(llm), InMemorySaver()), "t1", "I want a fridge")
    assert "quota is used up" in state["messages"][-1]["content"]
    assert state.get("phase") is None and world.crawls == []


def test_a_browser_problem_ends_the_turn_with_a_message():
    world = World(stored=3)

    def broken(profile, needs, page):
        raise RuntimeError("Target page, context or browser has been closed")

    world.crawl = broken
    llm = FakeLLM([CategoryChoice(category="fridge"), ready()])
    state = run(build_graph(world.deps(llm), InMemorySaver()), "t1", "A fridge with a freezer")
    assert "Something went wrong in the step 'search' (RuntimeError" in state["messages"][-1]["content"]
    assert world.picked == [] and state["needs"]["kind"] == "fridge_freezer"
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `python -m pytest tests/test_graph.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scout.agent.graph'`.

- [x] **Step 3: Write the state**

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
    picks: list[dict]  # listing_id, product_id, title, doubtful sizes and the note of each pick, in the order shown
    seen: list[int]    # product IDs shown since the needs last changed; "more" leaves them out
    searches: dict     # search terms -> {"page": the last result page read, "budget": the lowest budget of those reads}
    removed: list[dict]  # product_id, listing_id and title of each product that the shopper removed
    route: dict        # what the follow-up tools of this turn did, and the next step
```

- [x] **Step 4: Write the graph**

Create `src/scout/agent/graph.py`:

```python
"""The conversation graph: clarify, search, recommend, and the follow-up tools."""
from dataclasses import dataclass, field
from typing import Any, Callable

from langgraph.graph import END, START, StateGraph

from scout.agent.calc import calculate
from scout.agent.clarify import clarify, pick_category
from scout.agent.followup import act
from scout.agent.history import assistant, compact, history_text, last_user_text, user_numbers
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

AFTER_ACT = {"answer": END, "calculate": "calculate", "exclude": "recommend", "restore": "recommend",
             "change_needs": "clarify", "more": "search", "recheck": "recommend", "new_search": "reset", "stop": END}


@dataclass
class Deps:
    llm: Any
    profile_names: Callable[[], list[str]]
    load_profile: Callable[[str], CategoryProfile]
    count: Callable[[CategoryProfile, Needs, set[int]], int]
    crawl: Callable[[CategoryProfile, Needs, int], CrawlReport]
    # profile, needs, excluded product IDs, the cards that stay without a new check, the cards to check first
    picks: Callable[[CategoryProfile, Needs, set[int], list[Card], list[Card]], PicksResult]
    cards: Callable[[list[int]], list[Card]]
    eliminate: Callable[[str, list[int], str], None]
    eliminated: Callable[[str], set[int]]
    restore: Callable[[str, list[int]], None]
    say: Callable[[str], None] = print
    tracer: Any = field(default_factory=NoTracer)


def crawl_text(report: CrawlReport, terms: str, page: int) -> str:
    line = (f"Searched amazon.se for '{terms}' (result page {page}): {report.candidates} results, "
            f"{report.off_topic} left out by title, {report.over_budget} over budget, "
            f"{report.known} already stored (rating and review count updated), {report.saved} new products saved.")
    return "\n".join([line, *report.notes])


def higher_budget(new: float | None, old: float | None) -> bool:
    """True when the new budget lets in products that the old search left out. None means no budget."""
    if old is None:
        return False
    return new is None or new > old


def lower_budget(new: float | None, old: float | None) -> float | None:
    if new is None or old is None:
        return old if new is None else new
    return min(new, old)


def thread_of(config) -> str:
    return config["configurable"]["thread_id"]


def action_of(state) -> str | None:
    return (state.get("route") or {}).get("action")


def build_graph(deps: Deps, checkpointer=None):
    llm = deps.llm

    def guarded(name, fn):
        def node(state, config):
            with deps.tracer.span(name) as span:
                try:
                    return fn(state, config)
                except (LLMUnavailable, LLMOutputError) as exc:
                    text = f"I cannot finish this step now. {exc}"
                except Exception as exc:  # a browser or database problem must not end the conversation
                    span.update(level="ERROR", status_message=repr(exc))
                    text = (f"Something went wrong in the step '{name}' ({type(exc).__name__}: {exc}). "
                            "Your needs are kept. You can try again.")
                return {"messages": [assistant(text)], "route": {"action": "stop"}}
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
                return {"phase": "clarify", "route": {}, "messages": [assistant(
                    f"I can search these categories now: {', '.join(names)}. Which one do you want?")]}
            needs.category = category
        reply = clarify(llm, deps.load_profile(needs.category), needs, history, user_numbers(state))
        update = {"needs": reply.needs.model_dump(mode="json"), "route": {}}
        if reply.ready:
            return {**update, "phase": "search"}
        return {**update, "phase": "clarify", "messages": [assistant(reply.question)]}

    def search_node(state, config):
        needs = needs_of(state)
        profile = deps.load_profile(needs.category)
        terms = needs.search_terms(profile)
        searches = dict(state.get("searches") or {})
        last = searches.get(terms)
        conditions = f"Searching with: {needs.text() or 'no conditions'}."
        if last is None:
            page, why = 1, "the first search for these words in this conversation"
        elif higher_budget(needs.budget, last["budget"]):
            page, why = 1, "the budget is higher than in the earlier search"
        elif action_of(state) == "more":
            page, why = last["page"] + 1, "you asked for more"
        elif deps.count(profile, needs, deps.eliminated(thread_of(config))) < MIN_STORED:
            page, why = last["page"] + 1, f"fewer than {MIN_STORED} stored products match"
        else:
            return {"phase": "recommend", "messages": [assistant(
                f"{conditions} I searched amazon.se for '{terms}' earlier in this conversation, and enough "
                "stored products match, so I use them and check each pick again on amazon.se.")]}
        deps.say(f"Searching amazon.se for '{terms}', result page {page} ({why}). This can take a few minutes.")
        report = deps.crawl(profile, needs, page)
        budget = needs.budget if page == 1 else lower_budget(needs.budget, last["budget"])
        searches[terms] = {"page": page, "budget": budget}
        return {"phase": "recommend", "searches": searches,
                "messages": [assistant(f"{conditions}\n{crawl_text(report, terms, page)}")]}

    def marked_cards(picks: list[dict]) -> list[Card]:
        """The cards of the picks from the database, with the size doubts that the picks step found."""
        doubtful = {p["listing_id"]: set(p.get("doubtful", [])) for p in picks}
        cards = deps.cards([p["listing_id"] for p in picks])
        for card in cards:
            card.attributes = {k: ({**v, "doubtful": True} if k in doubtful[card.listing_id] else v)
                               for k, v in card.attributes.items()}
        return cards

    def recommend_node(state, config):
        needs, action = needs_of(state), action_of(state)
        excluded = set(deps.eliminated(thread_of(config)))
        shown = state.get("picks", [])
        kept: list[dict] = []
        first: list[Card] = []
        if action == "more":
            excluded |= set(state.get("seen", []))
        elif action in ("exclude", "restore"):
            kept = [p for p in shown if p["product_id"] not in excluded]
        if action == "restore":  # the products that came back are checked first
            first = deps.cards((state.get("route") or {}).get("restored", []))
        elif action == "recheck":
            first = marked_cards(shown)
        result = deps.picks(deps.load_profile(needs.category), needs, excluded, marked_cards(kept), first)
        if kept:  # the kept picks were not fetched again, so their notes come from the state
            result.card_notes = {**{p["listing_id"]: p["note"] for p in kept if p.get("note")}, **result.card_notes}
            result.notes += [p["note"] for p in kept if p.get("note")]
        picks = [{"listing_id": c.listing_id, "product_id": c.product_id, "title": c.title,
                  "doubtful": [k for k, v in c.attributes.items() if v.get("doubtful")],
                  "note": result.card_notes.get(c.listing_id)} for c in result.chosen]
        if action == "more" and not picks:  # keep the picks that the shopper saw
            return {"phase": "picks", "route": {}, "messages": [assistant(
                "No other stored product meets these needs. You can loosen a condition, or ask for more again.")]}
        ids = [p["product_id"] for p in picks]
        seen = ids if action not in ("more", "exclude", "restore", "recheck") else [*state.get("seen", []), *ids]
        text = result.render() if picks else ("No stored product meets these needs. You can loosen a condition, "
                                              "or ask me to search more.")
        return {"picks": picks, "seen": sorted(set(seen)), "phase": "picks", "route": {},
                "messages": [assistant(text)]}

    def act_node(state, config):
        picks, removed = state.get("picks", []), list(state.get("removed", []))
        allowed = user_numbers(state) | numbers_in(needs_of(state).text())
        result = act(llm, picks, marked_cards(picks), removed, history_text(state), allowed, last_user_text(state))
        if result is None:
            return {"route": {"action": "stop"}, "messages": [assistant(
                "I could not write a reply whose numbers and actions agree with the records, so I changed nothing. "
                "You can say it in other words.")]}
        route = {"action": result.action, "tools": result.tools, "pick_numbers": [], "restored": []}
        if result.question:
            return {"route": route, "messages": [assistant(result.question)]}
        thread, messages = thread_of(config), []
        if result.remove:  # Python says what it removed, so the text is always true
            chosen = [picks[n - 1] for n in result.remove.picks]
            deps.eliminate(thread, [p["product_id"] for p in chosen], result.remove.reason)
            removed += [{k: p[k] for k in ("product_id", "listing_id", "title")} for p in chosen]
            route["pick_numbers"] = result.remove.picks
            messages.append(assistant(f"Removed: {'; '.join(p['title'][:60] for p in chosen)}. "
                                      "They stay out for the rest of this conversation."))
        if result.restore:
            back = [r for r in removed if r["product_id"] in set(result.restore.product_ids)]
            deps.restore(thread, [r["product_id"] for r in back])
            removed = [r for r in removed if r not in back]
            route["restored"] = [r["listing_id"] for r in back]
            messages.append(assistant(f"Brought back: {'; '.join(r['title'][:60] for r in back)}."))
        if result.reply:
            messages.append(assistant(result.reply.text))
        return {"route": route, "removed": removed, "messages": messages}

    def calculate_node(state, config):
        text = calculate(llm, marked_cards(state.get("picks", [])), history_text(state), user_numbers(state))
        return {"messages": [assistant(text)]}

    def reset_node(state, config):
        return {"needs": {}, "picks": [], "seen": [], "removed": [], "phase": "clarify"}

    def unless_stopped(state):
        return END if action_of(state) == "stop" else "recommend"

    graph = StateGraph(ChatState)
    graph.add_node("compact", compact_node)
    for name, fn in [("clarify", clarify_node), ("search", search_node), ("recommend", recommend_node),
                     ("act", act_node), ("calculate", calculate_node), ("reset", reset_node)]:
        graph.add_node(name, guarded(name, fn))
    graph.add_edge(START, "compact")
    graph.add_conditional_edges("compact", lambda s: "act" if s.get("phase") == "picks" else "clarify",
                                ["clarify", "act"])
    graph.add_conditional_edges("clarify", lambda s: "search" if s.get("phase") == "search" else END,
                                ["search", END])
    graph.add_conditional_edges("search", unless_stopped, ["recommend", END])
    graph.add_edge("recommend", END)
    graph.add_conditional_edges("act", lambda s: s["route"]["action"], AFTER_ACT)
    graph.add_edge("calculate", END)
    graph.add_edge("reset", "clarify")
    return graph.compile(checkpointer=checkpointer)
```

- [x] **Step 5: Run the tests**

Run: `python -m pytest tests/test_graph.py -v`
Expected: 13 passed.

- [x] **Step 6: Run all tests**

Run: `python -m pytest -q`
Expected: all tests pass.

- [x] **Step 7: Commit**

```bash
git add src/scout/agent/state.py src/scout/agent/graph.py tests/test_graph.py
git commit -m "Conversation graph: clarify, search when stored data is not enough, recommend, follow-up tools"
```

---

### Task 10: `scout chat` with Postgres checkpoints

**Files:**
- Modify: `src/scout/db/conn.py` (`connect_checkpoints`)
- Create: `src/scout/agent/live.py`
- Modify: `src/scout/cli.py` (commands `chat` and `threads`)
- Create: `tests/test_chat_db.py`

- [x] **Step 1: Write the failing test**

Create `tests/test_chat_db.py`:

```python
import psycopg
import pytest
from langgraph.checkpoint.postgres import PostgresSaver

from scout.agent.clarify import CategoryChoice, ClarifyReply
from scout.agent.graph import build_graph
from scout.config import load_settings
from scout.db.conn import connect, connect_checkpoints, end_transaction
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


def test_a_failed_statement_does_not_block_the_next_turn():
    with connect(load_settings().test_database_url) as conn:
        with pytest.raises(psycopg.errors.UndefinedTable):
            conn.execute("SELECT * FROM no_such_table")
        end_transaction(conn)
        assert conn.execute("SELECT 1 AS one").fetchone()["one"] == 1
```

- [ ] **Step 2: Run the test to see it fail**

Run: `python -m pytest tests/test_chat_db.py -v`
Expected: FAIL with `ImportError: cannot import name 'connect_checkpoints'`.

- [x] **Step 3: Add the checkpoint connection**

Add to `src/scout/db/conn.py`:

```python
def end_transaction(conn: psycopg.Connection) -> None:
    """Close the open transaction after a chat turn, so that a failed statement does not block the next turn."""
    if conn.info.transaction_status == psycopg.pq.TransactionStatus.INERROR:
        conn.rollback()
    else:
        conn.commit()


def connect_checkpoints(url: str) -> psycopg.Connection:
    """A connection for the LangGraph checkpointer. It needs autocommit and dict rows."""
    return psycopg.connect(url, autocommit=True, prepare_threshold=0, row_factory=dict_row)
```

`end_transaction` runs after each turn. The connection of the agent is not in autocommit mode, so without it a read keeps a transaction open while the user types, and one failed statement blocks all later turns. A failed run leaves its step pending in the checkpoint. `update_state(..., as_node="calculate")` adds the message and ends that step, because `calculate` always leads to END. I checked this with `InMemorySaver` on 2026-10-05: a stopped search left `('search',)` pending, and after the update nothing was pending.

The checkpointer creates its own tables (`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations`) in the same database. `PostgresSaver.setup()` creates them when they do not exist.

- [x] **Step 4: Run the test**

Run: `python -m pytest tests/test_chat_db.py -v`
Expected: 2 passed.

- [x] **Step 5: Write the live dependencies**

Create `src/scout/agent/live.py`:

```python
"""The real dependencies of the chat graph: Postgres, the amazon.se browser and the LLM."""
from scout.agent.graph import Deps
from scout.crawl import crawl as run_crawl
from scout.db.store import add_eliminations, eliminated_products, restore_products
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
    screened: dict[tuple[str, int], bool] = {}  # a title is screened one time in a session

    def count(profile, needs, excluded):
        return len(shortlist(conn, profile, needs, llm, excluded, screened).ranked)

    def crawl(profile, needs, page):
        return run_crawl(conn, browser.adapter(), profile.category, needs.search_terms(profile),
                         on_problem=on_problem, first_page=page, max_pages=1, max_details=20,
                         budget=needs.budget_sek(), skip_known=True,
                         enrich=lambda rec: enrich_record(rec, profile, llm),
                         screen=lambda cands: screen_titles(cands, profile, llm, needs.wanted(profile)))

    def picks(profile, needs, excluded, kept, first):
        short = shortlist(conn, profile, needs, llm, set(excluded) | {c.product_id for c in [*kept, *first]}, screened)

        def refetch(card):  # the browser opens only when there is a card to check
            return refetcher(browser.adapter(), conn, profile.category, on_problem)(card)

        return make_picks(short, needs, k, refetch, llm, kept=kept, first=first)

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
        restore=lambda thread, ids: restore_products(conn, thread, ids),
        say=say,
        tracer=tracer,
    )
```

- [x] **Step 6: Add the commands**

Add to `src/scout/cli.py`:

```python
import contextlib
import uuid

from langgraph.checkpoint.postgres import PostgresSaver

from scout.agent.graph import build_graph
from scout.agent.history import assistant
from scout.agent.live import LazyBrowser, live_deps
from scout.db.conn import connect_checkpoints, end_transaction


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
                try:
                    with tracer.turn(thread_id, text) as span:
                        state = graph.invoke({"messages": [{"role": "user", "content": text}]}, config)
                        replies = [m["content"] for m in state["messages"][before + 1:]]
                        span.update(output="\n\n".join(replies))
                except Exception as exc:  # the nodes catch most problems; this keeps the conversation open
                    replies = [f"Something went wrong ({type(exc).__name__}: {exc}). You can try again."]
                    end_transaction(conn)
                    with contextlib.suppress(Exception):  # the thread gets the message, and the failed turn ends
                        graph.update_state(config, {"messages": [assistant(replies[0])]}, as_node="calculate")
                end_transaction(conn)
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

- [x] **Step 8: Commit**

```bash
git add src/scout/db/conn.py src/scout/agent/live.py src/scout/cli.py tests/test_chat_db.py
git commit -m "scout chat and scout threads: the graph with live dependencies and Postgres checkpoints"
```

---

### Task 11: The worker checks the sizes of each pick

The phase 1 check (`drop_implausible_sizes`) catches only sizes that cannot hold the volume. It did not catch the FOHERE 90 l fridge, whose page gives 48 cm height and 80 cm depth. I found this by hand on 2026-10-04. This task gives the worker the title, the bullet points and the specifications of each pick, in one call for all picks.

**Files:**
- Create: `src/scout/extract/doubt.py`
- Modify: `src/scout/picks/write.py` (show "(doubtful)")
- Modify: `src/scout/picks/pipeline.py`
- Create: `tests/test_doubt.py`

- [x] **Step 1: Write the failing tests**

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

- [x] **Step 3: Write the check**

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

- [x] **Step 4: Show doubtful values**

In `src/scout/picks/write.py`, use `attribute_text` (Task 3) in `_card_json` for the attributes:

```python
        "attributes": {k: attribute_text(v) for k, v in card.attributes.items()},
```

In `format_picks`, change the line that builds `attrs`:

```python
        attrs = ", ".join(f"{k}: {attribute_text(v)}" for k, v in card.attributes.items())
```

- [x] **Step 5: Check the sizes in the pipeline**

In `src/scout/picks/pipeline.py`, import `check_sizes` from `scout.extract.doubt`. Then change `make_picks`. It keeps the fresh records of the recheck, and it checks the sizes before it writes the summaries. A kept card was not fetched again, so it keeps the doubts of the turn that showed it (Task 9).

```python
def make_picks(short: Shortlist, needs: Needs, k: int, refetch: Refetch, llm, language: str = "English",
               kept: Sequence[Card] = (), first: Sequence[Card] = ()) -> PicksResult:
    """Recheck the top cards on the live pages, check their sizes, then write summaries with checked numbers.

    `kept` cards stay picks without a new check. `first` cards are checked before the shortlist. When kept and
    first are more than k, the last kept cards go.
    """
    kept = list(kept)[:max(0, k - len(first))]
    fetched: dict[int, ProductRecord] = {}

    def refetch_and_keep(card: Card) -> ProductRecord | None:
        rec = refetch(card)
        if rec is not None:
            fetched[card.listing_id] = rec
        return rec

    taken = {c.product_id for c in [*kept, *first]}
    ranked = [*first, *(c for c in short.ranked if c.product_id not in taken)]
    new, recheck_notes = recheck_picks(ranked, k - len(kept), refetch_and_keep, needs.budget_sek(),
                                       needs.rank_conditions())
    chosen = [*kept, *new]
    card_notes = {c.listing_id: short.size_notes[c.listing_id] for c in chosen if c.listing_id in short.size_notes}
    notes = short.notes + recheck_notes
    texts, errors = None, []
    if chosen and llm is not None:
        try:
            doubts = check_sizes([(c, fetched[c.listing_id]) for c in chosen if c.listing_id in fetched], llm)
            for listing_id, doubt in doubts.items():
                card_notes[listing_id] = f"{card_notes[listing_id]} {doubt}" if listing_id in card_notes else doubt
        except (LLMUnavailable, LLMOutputError) as exc:
            notes.append(f"Size check skipped: {exc}")
        try:
            texts, errors = write_picks(llm, chosen, needs.text(), language)
        except LLMUnavailable as exc:
            notes.append(f"No summaries: {exc}")
    notes += [card_notes[c.listing_id] for c in chosen if c.listing_id in card_notes]
    return PicksResult(chosen, texts, errors, notes, short.dropped, needs.text(), card_notes)
```

- [x] **Step 6: Run all tests**

Run: `python -m pytest -q`
Expected: all tests pass.

- [ ] **Step 7: Check the FOHERE page for real**

Run: `scout picks fridge --budget 9000 --where "height<=186" --where "width<=60"`
Expected: if the FOHERE 90 l fridge (B0F9Y2XN3J) is a pick, its height or depth shows "(doubtful)", and a note gives the reason. Write the result under this step, also when the worker does not flag it.

- [x] **Step 8: Commit**

```bash
git add src/scout/extract/doubt.py src/scout/picks tests/test_doubt.py
git commit -m "Worker size check on the picks: doubtful sizes are marked, quotes must be on the page"
```

---

### Task 12: Scripted conversations and the numbers

I asked for this in my review on 2026-10-05: three real conversations (Task 15) give too few numbers for the CV. This task adds 24 scripted conversations. They run the real chat model through the graph over the stored products. No browser opens, and a search only records its words and page. Python compares the values after each turn with the values that the script expects, and it counts the check rounds of each step. The run also tests a new chat model before it goes into `config/models.yaml` (decision 13).

**Files:**
- Modify: `config/models.yaml` (the `nvidia` provider)
- Create: `src/scout/eval/chat.py`, `eval/chat/conversations.yaml`, `tests/test_eval_chat.py`
- Modify: `src/scout/cli.py` (command `eval-chat`)

- [x] **Step 1: Write the failing tests**

Create `tests/test_eval_chat.py`:

```python
from scout.agent.clarify import CategoryChoice, ClarifyReply
from scout.agent.followup import RemovePicks
from scout.config import ROOT
from scout.eval.chat import Expect, Script, Turn, load_scripts, run_scripts, summarize
from scout.llm.client import QuotaExhausted
from scout.needs import Needs
from tests.fakes import FakeLLM
from tests.test_graph import World, ready


def test_a_script_is_scored_turn_by_turn():
    world = World()
    llm = FakeLLM([CategoryChoice(category="fridge"), ClarifyReply(needs=Needs(budget=9000), ready=False,
                                                                   question="Which kind?"),
                   ready(budget=9000), [RemovePicks(picks=[2], words="remove 2", one_pick=True)]])
    script = Script(id="s", turns=[
        Turn(say="a fridge, budget 9000", expect=Expect(phase="clarify", budget=9000, searched=False)),
        Turn(say="with a freezer", expect=Expect(phase="picks", kind="fridge_freezer", conditions=["height<=186"])),
        Turn(say="remove 2", expect=Expect(route="exclude", tools=["remove_picks"], pick_numbers=[2], says="Removed",
                                           not_says="Which pick")),
    ])
    records = run_scripts([script], world.deps(llm), world.crawls, llm)
    assert [c["ok"] for c in records[0]["checks"]] == [True, True, True]
    assert [(c["check"], c["ok"]) for c in records[1]["checks"]] == [("phase", True), ("kind", True),
                                                                      ("conditions", False)]
    assert all(c["ok"] for c in records[2]["checks"])
    assert records[0]["steps"] == {"clarify": {"rounds": 1, "passed": True, "first_round": True}}
    summary = summarize(records)
    assert summary["checks"]["conditions"] == {"ok": 0, "total": 1} and summary["errors"] == 0
    assert summary["steps"]["clarify"] == {"ran": 2, "first_round": 2, "passed": 2}


def test_the_scripts_load():
    scripts = load_scripts(ROOT / "eval" / "chat" / "conversations.yaml")
    assert len(scripts) == 24 and len({s.id for s in scripts}) == 24


def test_a_turn_that_a_node_ended_counts_as_an_error():
    world = World()
    llm = FakeLLM([QuotaExhausted("The free daily quota is used up.")])
    script = Script(id="s", turns=[Turn(say="a fridge", expect=Expect(phase="clarify"))])
    records = run_scripts([script], world.deps(llm), world.crawls, llm)
    assert records[0]["error"].startswith("I cannot finish this step now")
    assert summarize(records)["errors"] == 1
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `python -m pytest tests/test_eval_chat.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scout.eval.chat'`.

- [x] **Step 3: Write the evaluation**

Create `src/scout/eval/chat.py`:

```python
"""Scripted conversations: the real chat model talks with the graph over stored products, and Python scores it."""
import statistics
import time
from collections import Counter
from pathlib import Path

import yaml
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel, ConfigDict, Field

from scout.agent.graph import Deps, build_graph
from scout.crawl import CrawlReport
from scout.needs import Needs
from scout.picks.pipeline import make_picks, shortlist
from scout.picks.rank import Card, load_cards_by_ids
from scout.profiles import PROFILES_DIR, load_profile
from scout.sources.base import ProductRecord

# The steps whose output Python checks, by schema name, and the reply text when the last round still failed.
CHECKED = {"ClarifyReply": ("clarify", "The needs were still not valid"),
           "PicksText": ("summaries", "did not pass the checks"),
           "Act": ("actions", "so I changed nothing"),
           "CalcPlan": ("calculations", "I could not turn this request")}


NODE_ERRORS = ("I cannot finish this step now", "Something went wrong in the step")


class Expect(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a wrong key in the YAML must fail, not pass silently

    phase: str | None = None
    category: str | None = None             # "none": the agent must not choose a category
    kind: str | None = None
    budget: float | None = None
    no_budget: bool = False
    conditions: list[str] | None = None     # for example ["height<=186"]; [] means no conditions
    route: str | None = None                # the next step after the follow-up tools, or "answer"
    tools: list[str] | None = None          # the tools that the model called, in order
    pick_numbers: list[int] | None = None   # the picks that were removed
    searched: bool | None = None
    says: str | None = None                 # a text in the replies of the turn
    not_says: str | None = None             # a text that no reply of the turn may have


class Turn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    say: str
    expect: Expect = Field(default_factory=Expect)


class Script(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    note: str = ""
    turns: list[Turn]


def load_scripts(path: Path) -> list[Script]:
    return [Script.model_validate(s) for s in yaml.safe_load(path.read_text(encoding="utf-8"))]


def stored_record(card: Card) -> ProductRecord:
    """The stored facts of a card. The evaluation never opens amazon.se."""
    return ProductRecord(source="amazon_se", source_id=card.url.rstrip("/").split("/")[-1], url=card.url,
                         fetched_at=card.fetched_at, title=card.title, brand=card.brand, model=card.model,
                         price=card.price, currency=card.currency, in_stock=card.in_stock,
                         attributes=card.attributes)


def offline_deps(conn, llm, k: int = 5) -> tuple[Deps, list]:
    """Deps over the stored products. A search only records its words and page. Rejections stay in memory."""
    removed: dict[str, set[int]] = {}
    screened: dict[tuple[str, int], bool] = {}
    searches: list[tuple[str, int]] = []

    def crawl(profile, needs, page):
        searches.append((needs.search_terms(profile), page))
        return CrawlReport(notes=["Evaluation run: no search on amazon.se, only the stored products."])

    def picks(profile, needs, excluded, kept, first):
        short = shortlist(conn, profile, needs, llm, set(excluded) | {c.product_id for c in [*kept, *first]}, screened)
        return make_picks(short, needs, k, stored_record, llm, kept=kept, first=first)

    deps = Deps(
        llm=llm,
        profile_names=lambda: sorted(p.stem for p in PROFILES_DIR.glob("*.yaml")),
        load_profile=load_profile,
        count=lambda profile, needs, excluded: len(shortlist(conn, profile, needs, llm, excluded, screened).ranked),
        crawl=crawl,
        picks=picks,
        cards=lambda ids: load_cards_by_ids(conn, ids),
        eliminate=lambda thread, ids, reason: removed.setdefault(thread, set()).update(ids),
        eliminated=lambda thread: set(removed.get(thread, set())),
        restore=lambda thread, ids: removed.setdefault(thread, set()).difference_update(ids),
        say=lambda text: None,
    )
    return deps, searches


def compare(expect: Expect, state: dict, route: dict | None, searched: bool, replies: list[str]) -> list[dict]:
    """One entry for each expected value: its name, the expected and the actual value, and if they agree."""
    needs = Needs.model_validate(state.get("needs") or {})
    actual = {
        "phase": state.get("phase"),
        "category": needs.category or "none",
        "kind": needs.kind,
        "budget": needs.budget,
        "no_budget": needs.budget is None,
        "conditions": sorted(f"{c.attribute}{c.op}{c.value:g}" for c in needs.conditions),
        "route": (route or {}).get("action"),
        "tools": (route or {}).get("tools"),
        "pick_numbers": (route or {}).get("pick_numbers"),
        "searched": searched,
    }
    checks = []
    for name, wanted in expect.model_dump(exclude_none=True, exclude_defaults=True).items():
        if name in ("says", "not_says"):
            got = any(wanted in r for r in replies)
            ok = got if name == "says" else not got
            checks.append({"check": name, "expected": wanted, "actual": got, "ok": ok})
            continue
        if name == "conditions":
            wanted = sorted(wanted)
        checks.append({"check": name, "expected": wanted, "actual": actual[name], "ok": actual[name] == wanted})
    return checks


def run_scripts(scripts: list[Script], deps: Deps, searches: list, llm, progress=lambda text: None) -> list[dict]:
    """Run each script in a new thread. Return one record for each turn."""
    records = []
    for script in scripts:
        graph = build_graph(deps, InMemorySaver())
        config = {"configurable": {"thread_id": f"eval-{script.id}"}}
        for number, turn in enumerate(script.turns, 1):
            calls_before, searches_before, started = len(llm.calls), len(searches), time.monotonic()
            before = len(graph.get_state(config).values.get("messages", []))
            route, error = None, None
            try:
                for update in graph.stream({"messages": [{"role": "user", "content": turn.say}]}, config,
                                           stream_mode="updates"):
                    if (update.get("act") or {}).get("route"):
                        route = update["act"]["route"]
            except Exception as exc:  # report it, and go on with the next turn
                error = f"{type(exc).__name__}: {exc}"
            state = graph.get_state(config).values
            replies = [m["content"] for m in state.get("messages", [])[before + 1:]]
            # A node caught the problem (quota, server, browser), so the model was not measured in this turn.
            error = error or next((r for r in replies if r.startswith(NODE_ERRORS)), None)
            calls = llm.calls[calls_before:]
            steps = {}
            for schema, (step, failed_text) in CHECKED.items():
                rounds = sum(1 for c in calls if c.schema == schema and c.valid)
                if rounds:
                    passed = not any(failed_text in r for r in replies)
                    steps[step] = {"rounds": rounds, "passed": passed, "first_round": passed and rounds == 1}
            records.append({
                "script": script.id, "turn": number, "say": turn.say, "replies": replies, "error": error,
                "checks": compare(turn.expect, state, route, len(searches) > searches_before, replies),
                "steps": steps, "seconds": round(time.monotonic() - started, 1),
                "calls": [{"role": c.role, "model": c.model, "schema": c.schema, "valid": c.valid,
                           "input_tokens": c.input_tokens, "output_tokens": c.output_tokens} for c in calls],
            })
            failed = sum(not c["ok"] for c in records[-1]["checks"])
            progress(f"{script.id} turn {number}: {records[-1]['seconds']} s, {len(calls)} LLM calls, "
                     f"{failed} values do not agree{', error' if error else ''}")
    return records


def summarize(records: list[dict]) -> dict:
    checks = Counter()
    for record in records:
        for check in record["checks"]:
            checks[(check["check"], check["ok"])] += 1
    names = sorted({name for name, _ in checks})
    steps = {}
    for record in records:
        for step, result in record["steps"].items():
            entry = steps.setdefault(step, {"ran": 0, "first_round": 0, "passed": 0})
            entry["ran"] += 1
            entry["first_round"] += result["first_round"]
            entry["passed"] += result["passed"]
    models = {}
    for record in records:
        for call in record["calls"]:
            entry = models.setdefault(call["model"], {"calls": 0, "not_valid": 0, "input_tokens": 0, "output_tokens": 0})
            entry["calls"] += 1
            entry["not_valid"] += not call["valid"]
            entry["input_tokens"] += call["input_tokens"]
            entry["output_tokens"] += call["output_tokens"]
    seconds = [r["seconds"] for r in records]
    return {
        "turns": len(records),
        "errors": sum(1 for r in records if r["error"]),
        "checks": {name: {"ok": checks[(name, True)], "total": checks[(name, True)] + checks[(name, False)]}
                   for name in names},
        "steps": steps,
        "models": models,
        "seconds": {"median": statistics.median(seconds) if seconds else 0, "max": max(seconds, default=0)},
    }


def summary_text(summary: dict) -> str:
    lines = [f"turns {summary['turns']}, turns with an error {summary['errors']}, "
             f"seconds per turn: median {summary['seconds']['median']}, max {summary['seconds']['max']}",
             "Expected values:"]
    lines += [f"  {name}: {c['ok']} of {c['total']}" for name, c in summary["checks"].items()]
    lines.append("Checked steps (a round is one reply of the model that matched the schema):")
    lines += [f"  {step}: ran {s['ran']}, passed in round 1: {s['first_round']}, passed in the end: {s['passed']}"
              for step, s in summary["steps"].items()]
    lines.append("LLM calls:")
    lines += [f"  {model}: {m['calls']} calls ({m['not_valid']} not valid JSON), tokens in {m['input_tokens']}, "
              f"out {m['output_tokens']}" for model, m in summary["models"].items()]
    return "\n".join(lines)
```

A round is one reply of the model that matched the schema. A reply with wrong JSON does not count as a round, but the summary counts it per model.

- [x] **Step 4: Write the scripts**

Create `eval/chat/conversations.yaml`:

```yaml
# Scripted conversations for `scout eval-chat`. Each turn has the words of the shopper and the values that
# Python expects after the turn. I check the expected values before the first run.
# Expect keys: phase, category ("none" = no category), kind, budget, no_budget, conditions, route (the next step
# after the follow-up tools, or answer), tools (the tools called, in order), pick_numbers (the removed picks),
# searched (the turn started a search), says (a text in the replies), not_says (a text that no reply may have).

- id: zh-fridge-freezer
  note: The main conversation. The kind is missing in turn 1, so the agent must ask.
  turns:
    - say: 我想买个冰箱，放冰箱的地方宽60厘米、高186厘米，预算9000克朗
      expect: {phase: clarify, searched: false, budget: 9000, conditions: ["width<=60", "height<=186"]}
    - say: 要带冷冻室的。深度和噪音无所谓，直接搜吧
      expect: {phase: picks, searched: true, kind: fridge_freezer, budget: 9000,
               conditions: ["width<=60", "height<=186"]}
    - say: 第一个有多高？
      expect: {route: answer}
    - say: 把第二个去掉，评价太少了
      expect: {route: exclude, pick_numbers: [2]}
    - say: 第一个的价格加上499克朗运费是多少？
      expect: {route: calculate}

- id: en-mini-office
  turns:
    - say: I need a small fridge for my office desk. Budget 1500 kr. No other wishes, please search now.
      expect: {phase: picks, kind: mini_fridge, budget: 1500, conditions: []}

- id: sv-units
  note: 1,8 m must become 180 cm.
  turns:
    - say: Jag vill ha ett kylskåp med frys. Max 1,8 m högt och högst 55 cm brett. Sök direkt, inga andra önskemål.
      expect: {phase: picks, kind: fridge_freezer, no_budget: true, conditions: ["height<=180", "width<=55"]}

- id: en-family-of-4
  note: '"8k" is 8000. "4" people must not become a volume.'
  turns:
    - say: A fridge with a freezer for a family of 4. Budget 8k. Nothing else matters, search now.
      expect: {phase: picks, kind: fridge_freezer, budget: 8000, conditions: []}

- id: en-euro-budget
  note: A budget in euro must not become kronor without a rate.
  turns:
    - say: I want a fridge with a freezer, budget 800 euro.
      expect: {phase: clarify, no_budget: true, searched: false}

- id: zh-change-budget
  note: A higher budget searches page 1 again. After a lower budget, the agent searches only when fewer than 10 stored
    products match, and that depends on the database, so turn 3 does not expect a value for searched.
  turns:
    - say: 带冷冻室的冰箱，预算5000克朗，别的不限，直接搜
      expect: {phase: picks, kind: fridge_freezer, budget: 5000, searched: true}
    - say: 预算提高到7000
      expect: {route: change_needs, budget: 7000, searched: true}
    - say: 还是6000吧
      expect: {route: change_needs, budget: 6000}

- id: en-more
  turns:
    - say: A fridge with a freezer, any size, budget 9000. Search now.
      expect: {phase: picks, kind: fridge_freezer, budget: 9000}
    - say: Show me other options.
      expect: {route: more, searched: true}

- id: en-recheck
  turns:
    - say: A fridge with a freezer, any size, budget 9000. Search now.
      expect: {phase: picks}
    - say: Are these prices still correct?
      expect: {route: recheck, searched: false}

- id: en-new-category
  turns:
    - say: A fridge with a freezer, any size, budget 9000. Search now.
      expect: {phase: picks}
    - say: Thanks. Now I need headphones.
      expect: {route: new_search, category: none, says: I can search these categories now}

- id: sv-sofa
  turns:
    - say: Jag letar efter en soffa.
      expect: {category: none, says: I can search these categories now}

- id: en-unclear-exclude
  note: No pick number, so the agent must ask which pick. It can ask through the exclude step or the answer step.
  turns:
    - say: A fridge with a freezer, any size, budget 9000. Search now.
      expect: {phase: picks}
    - say: Remove that one.
      expect: {says: Which, not_says: Removed}

- id: zh-wine-cooler
  note: '"20 bottles" is not an attribute, so there is no condition.'
  turns:
    - say: 想要一个酒柜，放20瓶左右，预算5000克朗，别的都不重要，直接搜
      expect: {phase: picks, kind: wine_cooler, budget: 5000, conditions: []}

# References to picks. The first turn gives the same 5 picks from the stored products:
# 1 Severin (red, 61.5 cm wide), 2 Bomann 175 L (red, 49.5 cm), 3 Bomann side-by-side (90 cm),
# 4 FOHERE 90 L (41 dB), 5 Bomann KG7357 (no sizes on its card).
- id: en-ref-it
  note: '"it" is the pick of the turn before.'
  turns:
    - say: A fridge with a freezer, any size, budget 9000. Search now.
      expect: {phase: picks}
    - say: How loud is pick 4?
      expect: {route: answer}
    - say: Too loud for me. Remove it.
      expect: {route: exclude, pick_numbers: [4], not_says: Which}

- id: en-ref-brand
  note: Only pick 1 is a Severin.
  turns:
    - say: A fridge with a freezer, any size, budget 9000. Search now.
      expect: {phase: picks}
    - say: I don't want the Severin.
      expect: {route: exclude, pick_numbers: [1], not_says: Which}

- id: en-ref-widest
  note: Pick 3 is 90 cm wide. Its title says so.
  turns:
    - say: A fridge with a freezer, any size, budget 9000. Search now.
      expect: {phase: picks}
    - say: Remove the widest one.
      expect: {route: exclude, pick_numbers: [3], not_says: Which}

- id: en-ref-no-sizes
  note: Only the card of pick 5 has no sizes. Its title does not say so.
  turns:
    - say: A fridge with a freezer, any size, budget 9000. Search now.
      expect: {phase: picks}
    - say: Drop the one without dimensions.
      expect: {route: exclude, pick_numbers: [5], not_says: Which}

- id: zh-ref-second
  turns:
    - say: 要一个带冷冻的冰箱，尺寸无所谓，预算9000克朗，直接搜
      expect: {phase: picks}
    - say: 第二个我不要
      expect: {route: exclude, pick_numbers: [2], not_says: Which}

- id: en-unclear-brand
  note: Picks 2, 3 and 5 are Bomann, and the shopper says "the Bomann". The agent must ask.
  turns:
    - say: A fridge with a freezer, any size, budget 9000. Search now.
      expect: {phase: picks}
    - say: Remove the Bomann.
      expect: {says: Which, not_says: Removed}

- id: zh-unclear-red
  note: Picks 1 and 2 are red. The agent must ask.
  turns:
    - say: 要一个带冷冻的冰箱，尺寸无所谓，预算9000克朗，直接搜
      expect: {phase: picks}
    - say: 去掉红色那个
      expect: {says: Which, not_says: Removed}

# Free wording. The same 5 picks as above.
- id: en-two-things
  note: One message removes a pick and asks for more.
  turns:
    - say: A fridge with a freezer, any size, budget 9000. Search now.
      expect: {phase: picks}
    - say: Drop the FOHERE and show me some more.
      expect: {route: more, pick_numbers: [4], searched: true}

- id: zh-two-things
  note: One message removes a pick and lowers the budget.
  turns:
    - say: 要一个带冷冻的冰箱，尺寸无所谓，预算9000克朗，直接搜
      expect: {phase: picks}
    - say: 第3个太宽了不要，预算降到6000
      expect: {route: change_needs, pick_numbers: [3], budget: 6000}

- id: en-undo
  note: The shopper takes a removal back.
  turns:
    - say: A fridge with a freezer, any size, budget 9000. Search now.
      expect: {phase: picks}
    - say: Remove pick 1.
      expect: {route: exclude, pick_numbers: [1]}
    - say: Sorry, I want the Severin back.
      expect: {route: restore, says: Brought back}

- id: en-why
  note: A question about the order. The agent only answers.
  turns:
    - say: A fridge with a freezer, any size, budget 9000. Search now.
      expect: {phase: picks}
    - say: Why is the Severin first and not the Bomann with 1779 ratings?
      expect: {route: answer, not_says: Removed}

- id: en-off-topic
  turns:
    - say: A fridge with a freezer, any size, budget 9000. Search now.
      expect: {phase: picks}
    - say: By the way, what will the weather be in Stockholm tomorrow?
      expect: {route: answer, not_says: Removed}
```

I check the expected values before the numbers go anywhere. A wrong expected value makes a correct agent look wrong.

- [x] **Step 5: Add the command and the NVIDIA provider**

In `config/models.yaml`, add under `providers`:

```yaml
  nvidia:
    base_url: https://integrate.api.nvidia.com/v1
    api_key_env: NVIDIA_API_KEY
```

I made the free key on build.nvidia.com on 2026-10-05. It is `NVIDIA_API_KEY` in `.env`.

In `src/scout/cli.py`, add `import re`, import `load_scripts`, `offline_deps`, `run_scripts`, `summarize` and `summary_text` from `scout.eval.chat`, and `RoleConfig` from `scout.llm.config`. Then add:

```python
@app.command("eval-chat")
def eval_chat(
    scripts: Path = ROOT / "eval" / "chat" / "conversations.yaml",
    chat: Optional[str] = typer.Option(None, help="provider:model for the chat role, for example "
                                                  "nvidia:nvidia/nemotron-3-ultra-550b-a55b"),
    rpm: int = 10,
    only: Optional[str] = typer.Option(None, help="run only the scripts with these ids, separated by commas"),
    k: int = 5,
) -> None:
    """Run the scripted conversations over the stored products, and save the numbers. No browser opens."""
    settings = load_settings()
    config = load_llm_config()
    if chat:
        provider, model = chat.split(":", 1)
        config.roles["chat"] = RoleConfig(provider=provider, model=model, rpm=rpm)
    config.roles["chat"].fallback = None  # one chat model for each run, so the numbers belong to it
    llm = LLM(config)
    todo = [s for s in load_scripts(scripts) if only is None or s.id in only.split(",")]
    with connect(settings.database_url) as conn:
        deps, searches = offline_deps(conn, llm, k)
        records = run_scripts(todo, deps, searches, llm, typer.echo)
    summary = {"chat_model": llm.model_for("chat"), "worker_model": llm.model_for("worker"),
               "run_at": datetime.now(timezone.utc).isoformat(), **summarize(records)}
    slug = re.sub(r"[^\w.-]", "_", llm.model_for("chat"))  # "a/b:free" is not a file name on Windows
    out = ROOT / "eval" / "results" / f"chat-{slug}-{datetime.now():%Y%m%d-%H%M}.json"
    out.write_text(json.dumps({"summary": summary, "turns": records}, indent=2, ensure_ascii=False), encoding="utf-8")
    for record in records:
        for check in record["checks"]:
            if not check["ok"]:
                typer.echo(f"{record['script']} turn {record['turn']}: {check['check']} expected "
                           f"{check['expected']}, got {check['actual']}")
        if record["error"]:
            typer.echo(f"{record['script']} turn {record['turn']}: error {record['error']}")
    typer.echo(summary_text(summary))
    typer.echo(f"saved {out}")
```

- [x] **Step 6: Run all tests**

Run: `python -m pytest -q`
Expected: all tests pass.

- [ ] **Step 7: Run the scripts with two chat models**

Run: `scout eval-chat`, then `scout eval-chat --chat nvidia:nvidia/nemotron-3-ultra-550b-a55b --rpm 30`
Expected: each run prints the values that did not agree, the summary, and the path of the JSON file in `eval/results/`. Write the numbers under this step.

**Dry-run results, 2026-10-05.** I ran the scripts on the code that this plan makes, in a temporary worktree, over the 24 stored listings. The worker was `gemini-3.5-flash-lite`.

`nvidia/nemotron-3-ultra-550b-a55b` (NVIDIA free API), all 12 scripts, 22 turns, no turn with an error:

| Measure | Result |
|---|---|
| Expected values that agree | 37 of 38 (budget 9/9, conditions 6/6, kind 7/7, phase 12/12, route 8/8, pick numbers 1/1, searched 7/7, category 2/2, no budget 2/2, says 2/3) |
| Clarify steps that passed in round 1 | 14 of 14 |
| Summaries that passed in round 1 | 10 of 14; all 14 passed in the end |
| Answers that passed in round 1 | 2 of 2 |
| Calculations that passed in round 1 | 0 of 1; it passed in round 2 |
| Chat calls | 59 (1 reply was not valid JSON), 61,362 tokens in, 58,692 out |
| Worker calls | 17, 10,016 tokens in, 1,232 out |
| Seconds per turn | median 80.9, maximum 480.6 |

The value that did not agree shows a real fault. The shopper wrote "Remove that one." The router chose `answer`, and the answer said "Removed Pick 5", but nothing was removed. I then changed two prompts in Task 7: the router chooses `exclude` also without a number, so Python asks which pick, and the answer step must never say that it changed the picks. After that change, this script passed (2 turns).

Earlier runs on the same day found four faults that this plan now fixes:
- The calculation tool had no retry. The model put a whole formula into one number field, and the shopper saw a raw error (Task 8).
- The clarify step did not see that "a small fridge for my office desk" is a mini fridge, and it asked for the kind again (Task 6).
- The clarify step chose its own search words ("kylfrys" in place of "kyl och frys") (Task 6).
- One hung request held a run for many minutes, because the OpenAI library waits 600 seconds and tries 2 more times (Task 1).
Two expected values in the scripts were wrong, and I changed them: after a lower budget the agent searches again when fewer than 10 stored products match, and that depends on the database; and the agent can ask "which pick" through the answer step. These are in the notes of the scripts.

`gemini-3.8-flash`: the free daily quota of the chat model ran out after the first script. The first script (5 turns) gave 13 of 13 expected values, at 29 to 107 seconds a turn. The quota was already used on 2026-10-04, so this run says nothing about the daily limit. Run the full comparison on a day with a new quota.

The results above are for the router version of Task 7: the model chose one `Route` object for each turn.

**Router version, the same 8 scripts with each model, 2026-10-05.** These are the 8 scripts that refer to picks without a number (`en-unclear-exclude`, the 5 `ref` scripts, `en-unclear-brand`, `zh-unclear-red`), 17 turns. The table counts the expected values of the follow-up turns.

| Chat model | Route | Pick numbers | "says" | "not says" | Seconds per turn (median) |
|---|---|---|---|---|---|
| `nvidia/nemotron-3-ultra-550b-a55b` | 6 of 6 | 5 of 5 | 2 of 3 | 7 of 8 | 104.2 |
| `qwen/qwen3.8-27b:free` (OpenRouter) | 6 of 6 | 5 of 5 | 1 of 3 | 6 of 8 | 38.8 |
| `ministral-14b-latest` (Mistral free) | 1 of 6 | 0 of 5 | 0 of 3 | 8 of 8 | 4.3 |

The misses of Nemotron and Qwen are the same kind: the words fit several picks ("the red one"), and the model guessed one or removed all. The Groq `openai/gpt-oss-120b` run of the tool-call version is not valid: the daily quota ran out, and the program of that time counted the failed turns as model faults.

**Tool-call version in the repository, 2026-10-05.** Nemotron, 13 scripts (the 8 above and the 5 free-wording scripts), 28 turns. The first run found that Nemotron writes its tool calls as text with outer brackets that do not match, so 33 of 96 calls counted as "no tool call". I changed the client to read each JSON object on its own, and to show the model its last reply on a retry. The second run, on commit 2d76349 with the review fixes:

| Measure | Result |
|---|---|
| Turns not measured | 1 of 28 (the NVIDIA rate limit) |
| Expected values that agree | route 11 of 11, pick numbers 7 of 7, picks 11 of 11, phase 13 of 13, budget 1 of 1, searched 1 of 1, says 2 of 4, not says 7 of 9 |
| Actions (tool calls) that passed in round 1 | 12 of 14; all 14 passed in the end |
| Summaries that passed in round 1 | 12 of 23; all 23 passed in the end |
| Chat calls | 78 (0 not valid), 103,762 tokens in, 105,020 out |
| Seconds per turn | median 94.5, maximum 291.4 |

The 5 free-wording scripts all passed: "Drop the FOHERE and show me some more" removed pick 4 and showed new products, "第3个太宽了不要，预算降到6000" removed pick 3 and changed the budget, the undo brought the Severin back, and the why and off-topic questions only got a reply. The router version cannot do two things in one message. On the 8 scripts of the table, the tool-call version gave route 5 of 5, pick numbers 4 of 4, says 1 of 3 and not says 5 of 7 (1 turn not measured).

The 2 values that did not agree are the same fault in 2 scripts. "Remove the Bomann" (3 picks are Bomann) and "去掉红色那个" ("remove that red one", 2 picks are red) removed all picks that fit. The model set `one_pick` to false, so Python did not ask. The results file is `eval/results/chat-nvidia_nemotron-3-ultra-550b-a55b-20261005-0657.json`.

**Decisions to make.**
1. The chat model. Nemotron has the best judgement of the free models, but a turn takes about 95 seconds. I bring a stronger API key on 2026-10-06. Run all 24 scripts with it and with `gemini-3.8-flash` after the quota resets, then choose the main model and the fallback in `config/models.yaml`.
2. Removing several picks. When the model removes 2 or more picks and the shopper did not give their numbers, Python could ask first: "Do you mean all of these: pick 2, 3 and 5, or one of them?" This costs one more turn when the shopper meant all. I did not build this. Run the stronger model first: if it sets `one_pick` correctly, this check is not necessary.

- [x] **Step 8: Commit**

```bash
git add config/models.yaml src/scout/eval/chat.py src/scout/cli.py eval/chat tests/test_eval_chat.py eval/results
git commit -m "Scripted conversations: eval-chat scores the needs, the tool calls and the check rounds of each chat model"
```

---

### Task 13: Read the price when amazon.se hides it

**Why:** I reviewed the 32 labels that an LLM made, on 2026-10-04. 27 were correct. 5 were wrong, and in all 5 the page shows no single price:

1. **Offer panel (4 pages).** B07YKSGPV1, B093388SWG, B0B5LN7J3X and B0DRVXYFXD show no buy box. The page says "Priset är högre än vanligt" and shows the button "Visa alla köpalternativ". The offer panel behind the button has the prices. The panel does not show the stock. My rule: an offer that you can add to the cart is in stock.
2. **Size choice (1 page).** B0H4YNM2BL shows a price range, 1809 to 1977 kr, and "Välj Storlek". The page shows a price and a stock only after a size is selected. The page has an ASIN for each colour and size (`dimensionValuesDisplayData`, here 4 ASINs for 2 colours and 2 sizes).

Now these products have no price, so a budget removes them from the picks, although they can be bought. The labels and the parser both said "no price", so the evaluation counted them as correct.

**Note.** The dry run did not include this task. The saved pages do not contain the offer panel: it loads only after a click (the saved pages have the class `aod-assets-not-loaded`). So the selectors and the test fixtures come from real pages that Step 1 saves. I write the code of Steps 2 to 6 after Step 1, with the same rules as the other tasks: a test first, then the code.

**Files:**
- Modify: `src/scout/sources/base.py`, `src/scout/sources/amazon_se/product.py`, `src/scout/sources/amazon_se/adapter.py`, `src/scout/sources/browser.py`
- Create: `src/scout/sources/amazon_se/offers.py`, `tests/fixtures/amazon_se/offers_*.html`, `tests/fixtures/amazon_se/product_size_*.html`, `tests/test_amazon_offers.py`
- Modify: `src/scout/crawl.py`, `src/scout/picks/recheck.py`, `src/scout/picks/write.py`, `src/scout/db/schema.sql`, `src/scout/db/store.py`

- [ ] **Step 1: Save real pages after the click**

Add `--offers` and `--sizes` to `scout snap`.
- With `--offers`, the browser opens the product page, clicks "Visa alla köpalternativ", waits up to 10 seconds for `#aod-container`, and saves the panel HTML and a screenshot in `snapshots/amazon_se/offers/<ASIN>/`.
- With `--sizes`, the browser opens the page of each size option by its own ASIN (`/dp/<ASIN>?th=1&psc=1`, at most 4), and saves the page HTML and a screenshot in `snapshots/amazon_se/product/<ASIN>/`. Check on these pages that one size is selected and that the price and the stock show. If they do not show, the adapter clicks the option in the row "Storlek" instead, and this step saves the page after each click.

Run `--offers` on the 4 offer-panel ASINs and `--sizes` on B0H4YNM2BL. I am at the computer, because a captcha can come. Copy reduced versions to `tests/fixtures/amazon_se/` (remove scripts and images, keep the offer blocks and the price blocks). One offer fixture must have a used offer, if a real panel has one.

- [ ] **Step 2: Detect a hidden price**

`ProductRecord` gets `price_hidden: Literal["offers", "size"] | None = None` and `price_source: Literal["buy_box", "all_offers", "size_choice"] | None = None`.
- `offers`: the parser finds no price and finds `#buybox-see-all-buying-choices`.
- `size`: the parser finds `.a-price-range` and a row with "Välj". It also reads the ASIN, colour and size of each option from `dimensionValuesDisplayData`. `ProductRecord` gets `size_options: list[SizeOption]` (ASIN, colour, size).

Test this on the 38 labelled pages: the 4 offer pages give `offers`, B0H4YNM2BL gives `size`, and the other 33 give None.

- [ ] **Step 3: Parse the offer panel**

`parse_offers(html) -> list[Offer]`. `Offer` has `price`, `currency`, `condition` ("new" or "used"), `seller`, `delivery` (text, for example "Gratis leverans"), `can_add_to_cart` and `raw_text`. Python finds each `raw_text` on the panel, as for the other extracted values. `lowest_new(offers)` returns the cheapest offer in the condition "new" that can go in the cart, or None. Test: the fixture with a used offer never returns the used price.

- [ ] **Step 4: Use the offer panel and the size choice in the crawl and the recheck**

- `offers`: the adapter opens the panel in the same tab (one click, no new page load). The record takes the price of `lowest_new`, with `price_source="all_offers"`, and `in_stock` is True. When the panel has no new offer that can go in the cart, the price stays empty, as now.
- `size`: each size option of the colour on the page, at most 4, becomes its own listing with its own product page, as the colours already are. The crawl adds the option ASINs to the candidates, and `known_source_ids` skips the stored ones. A size has its own measurements, so it must not share the attributes of another size. Its price gets `price_source="size_choice"`. `load_cards` needs no change, because each listing then has one size.
- The fetch log gets one line for each click. `variants` and `price_history` get the column `price_source`. The recheck of the picks does the same clicks.

- [ ] **Step 5: Show it in the picks**

- A card with `price_source="all_offers"` shows "(from all buying options, Amazon marks this price as higher than usual)" after the price.
- A card with `price_source="size_choice"` shows the size after the price, for example "1 809,00 kr (size 50 L)".

`check_picks` accepts the price as before. Test with two cards in `tests/test_picks_write.py`.

- [ ] **Step 6: New labels and a new evaluation run (by hand)**

I label the 4 offer panels (the cheapest new offer that can go in the cart, in kr) and each size of B0H4YNM2BL (price and stock), from the screenshots of Step 1. Add these rows to `data/eval/price_labels_all.csv` with the note "labelled by hand after the click". Run `scout eval-prices`. Write the result under this step.

- [ ] **Step 7: Commit**

```bash
git add src tests
git commit -m "Read the price from the offer panel or the size choice when amazon.se hides it"
```

---

### Task 14: Langfuse setup

- [ ] **Step 1: Choose where the traces go**

I chose **Langfuse Cloud, EU region, free Hobby plan**. A self-hosted Langfuse (version 3 and later) needs Postgres, ClickHouse, Redis and S3-compatible storage, which is a heavy load on the laptop. The traces contain the conversation and the product facts. This is the same kind of data that already goes to Gemini and Groq. Check the limits of the Hobby plan on the pricing page on the day that you sign up, and write them here. Never add a payment card.

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

### Task 15: Real conversations and the numbers

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

- [ ] **Step 5: Measure **

Run `scout eval-chat` again with the chat model of `config/models.yaml`, and compare the numbers with Task 12. From the terminal output and from Langfuse, write a table for conversations A and B:
- turns, and LLM calls and tokens for each model;
- seconds for each turn (median and maximum), and which step took the most time;
- product pages fetched;
- answers and summaries that failed the number check in their first round, and how many passed later;
- size doubts, and for each one whether I agree after I look at the page.

Report only the real numbers. Nothing goes into the CV until I confirm it.

- [ ] **Step 6: Update the documents**

1. Spec: write the 15 decisions of this plan into sections 3, 4.1, 4.2, 4.3, 4.7, 4.8 and 4.11, and the scripted conversations into section 6 (evaluation). In the table of LLM candidates, change the Groq row: it is the `chat` fallback, with the limits from 2026-10-04.
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
- Measure how many result pages give good picks: after which page do the top 5 stop changing? I decided on 2026-10-04 to do this after the reviews are connected, because then the candidates come from the search and from professional reviews.
- Store the title screening results, so that `--no-llm` runs and later searches do not screen the same titles again (I decided on 2026-10-04 to keep the LLM screening for now).
