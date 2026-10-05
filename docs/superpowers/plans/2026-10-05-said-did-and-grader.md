# Said Equals Did, Free Calculations and a Reply Grader

> **How to use this plan.** Zheyun reviews this plan first. Claude starts only after she agrees, and makes the changes that she asks for. Do the tasks in order and commit at the end of each task. Do not run `scout eval-chat` with a paid model until she says so.

**Goal:** Give the chat model more freedom, and measure the quality of its replies with a second model. Python keeps the checks that a rule can do well: the facts in each reply agree with the records, and each action that a reply states has a tool call. The model calculates by itself. A grader model scores each reply against a rubric, and Zheyun's own labels show how far the grader is correct.

**Why:** The run with GPT-5.6 Luna on 2026-10-05 (`eval/results/chat-openai_gpt-5.6-luna-20261005-1844.json`) showed four faults:

1. A reply said "I'll recheck" but called no tool. Nothing runs after a reply, so the promise is false.
2. A calculation reply did not pass the number check, possibly because the sum (5648.51 + 499 = 6147.51) was not one of the allowed numbers. The results file does not keep the rejected rounds, so this is not sure.
3. "Remove the red one" removed all red picks.
4. An off-topic question started a search.

The number rule ("each new number must come from a claim, the shopper or Python") is too strict for calculations. A rule cannot list all the correct ways to calculate. Zheyun decided: the model calculates, and a grader with a rubric checks the quality.

**Spec changes (for Zheyun to agree).** Task 4 changes these principles of the design spec and of `CLAUDE.md`:

- "Python calculates only what the user specifies." This becomes: "The model calculates. It uses only the costs and rates that the user gives. The grader checks the arithmetic."
- Spec section 4.7 table, the `calculate` row, and section 6, "Calculation correctness".
- Not changed: there is still no default conversion, shipping or duty. Facts (price, rating, sizes) are still checked by Python against the records.

## Decisions for Zheyun

1. **The calculation tool.** Recommended: remove `calculate` from `next_step`, so the model answers calculations in its reply. Keep `agent/calc.py` until Task 7 shows the grader results for arithmetic, then delete it. Other choice: keep the tool for long rules ("25% moms and 499 SEK shipping"), and let the model calculate only short sums.
2. **Where the reply labels go.** The labels hold product titles and prices from amazon.se, as the results files in `eval/results/` already do. Recommended: `eval/labels/replies.yaml` in Git, so the calibration numbers can be checked again. Other choice: `data/labels/`, which Git ignores.
3. **When a grader is good enough.** Recommended: agreement with her labels of 85% or more for each criterion, and the grader says "pass" for at most 1 in 10 of the replies that she marked "fail" on `facts` and `said_did`. Of the grader models that meet these limits, use the cheapest.
4. **The judge models to compare.** Recommended: four models from three companies other than OpenAI (Luna is an OpenAI model, and a judge can prefer the replies of its own family). Prices are per million tokens, input and output, from the OpenRouter list on 2026-10-05. The cost of one run is an estimate for 48 turns at about 3,500 tokens in and 400 tokens out each, without reasoning tokens.

| Judge | Input USD | Output USD | One graded run, about |
|---|---|---|---|
| `deepseek/deepseek-v4-flash` | 0.03 | 1.28 | 0.03 USD |
| `google/gemini-3.8-flash` | 0.75 | 3.75 | 0.20 USD (or the free Gemini key, if the daily quota is enough) |
| `anthropic/claude-haiku-4.5` | 1.00 | 5.00 | 0.27 USD |
| `anthropic/claude-sonnet-5.5` | 2.00 | 10.00 | 0.53 USD (the reference) |

The calibration of all four on 50 labelled replies costs about 1 USD.

## Global Constraints

- The facts check stays: each claim about price, rating, review count or a card attribute must agree with the card.
- The summaries of the picks (`picks/write.py`) keep the strict number rule. They state facts, not calculations.
- The model never adds conversion, shipping, duty or moms that the shopper did not give.
- Python never types passwords and never bypasses captchas. Not changed by this plan.
- Never print API key values.

## File Structure

| File | Change |
|---|---|
| `src/scout/llm/client.py` | `CallRecord` keeps the reply text and the problems of each round |
| `src/scout/agent/followup.py` | Promise check, ask before removing several picks, `derived` numbers in `Reply` |
| `src/scout/agent/graph.py` | No `calculate` route (if decision 1 is the recommended choice) |
| `src/scout/eval/chat.py` | Each turn record keeps the cards, the actions and the rounds |
| `src/scout/eval/grade.py` | New. The rubric, the grader call, the agreement numbers |
| `src/scout/cli.py` | `scout grade`, `scout label`, `scout calibrate` |
| `config/models.yaml` | New role `judge` |
| `eval/labels/replies.yaml` | New. Zheyun's labels (decision 2) |
| `tests/` | Tests for each change |

---

### Task 1: Keep the rejected rounds in the results

The results file must show why Python rejected a round. Without this, fault 2 above cannot be explained.

**Files:** `src/scout/llm/client.py`, `src/scout/agent/followup.py`, `src/scout/agent/clarify.py`, `src/scout/picks/write.py`, `src/scout/agent/calc.py`, `src/scout/eval/chat.py`, tests.

- [ ] **Step 1: Write the failing tests.** In `tests/test_eval_chat.py`, a script whose first act round has a number that is not allowed. The turn record has `rounds` with 2 items: the first has `passed: false`, the problem text and the reply text; the second has `passed: true`.

- [ ] **Step 2: Keep the output and the problems in the call record.**

```python
@dataclass
class CallRecord:
    ...
    checked: bool | None = None
    output: str = ""  # the reply text or the tool calls as JSON
    problems: list[str] = field(default_factory=list)  # what the checking step found
```

`_structured` sets `output` to the text. `_tool_calls` sets it to `json.dumps(raw)`. `mark_checked(passed, problems=())` also sets `problems`. Each checked step passes its error list.

- [ ] **Step 3: Write the rounds into each turn record.** In `run_scripts`, add for each call with `checked is not None`:

```python
"rounds": [{"schema": c.schema, "passed": c.checked, "problems": c.problems, "output": c.output}
           for c in calls if c.checked is not None],
```

- [ ] **Step 4: Run the tests.** `python -m pytest`. All pass.

- [ ] **Step 5: Commit.**

---

### Task 2: A reply that states or promises an action needs the tool call

Now Python checks only the past tense ("removed", "brought back"). Add the promises ("I'll recheck", "let me search") and the other tools. Python does not decide what to do. It tells the model the problem, and the model calls the tool or changes the text.

**Files:** `src/scout/agent/followup.py`, `tests/test_followup.py`.

- [ ] **Step 1: Write the failing tests.** Each of these replies with no matching tool call gives one problem, and with the matching call gives none:

| Reply text | Needs |
|---|---|
| "I'll recheck the prices now." | `next_step` recheck |
| "Let me search for more kettles." | `next_step` show_more or new_search |
| "I will remove the Bomann." | `remove_picks` |
| "I rechecked the stock." | `next_step` recheck |
| "Do you want me to recheck the prices?" | nothing (a question) |
| "I cannot recheck now, the prices are from 3 October." | nothing (no promise) |

- [ ] **Step 2: Map the words to the tools.**

```python
# Words that state or promise an action, and the tool calls that do it.
ACTION_WORDS = [
    (re.compile(r"\b(rechecked|re-checked|recheck|re-check|check(ed)? (again|the (prices?|stock)))\b", re.I),
     {"next_step:recheck"}),
    (re.compile(r"\b(search(ed)?|look(ed)? for|find|found) (more|other|new)\b", re.I),
     {"next_step:show_more", "next_step:new_search"}),
    (re.compile(r"\b(remove[ds]?|delete[ds]?|exclude[ds]?|drop(ped|s)?|t(ook|ake) out)\b", re.I),
     {"remove_picks"}),
    (re.compile(r"\b(restored?|br(ing|ought) back|add(ed)? back|put back)\b", re.I), {"restore_picks"}),
]
PROMISE = re.compile(r"\b(I'?ll|I will|let me|I am going to|I'm going to|I'?ve|I have|I)\b", re.I)


def unbacked_actions(text: str, done: set[str]) -> list[str]:
    """Actions that a statement in the reply says were done or will be done, with no tool call that does them."""
    missing = []
    for sentence in re.findall(r"[^.!?\n]+[.!?]?", text):
        if sentence.strip().endswith("?") or not PROMISE.search(sentence) or NEGATION.search(sentence):
            continue
        for pattern, tools in ACTION_WORDS:
            if (m := pattern.search(sentence)) and not tools & done:
                missing.append(m.group(0))
    return missing
```

`done` holds the names of the calls of the round, with the step for `next_step`, for example `"next_step:recheck"`. `NEGATION` matches "cannot", "can't", "do not", "did not", "not". This replaces `says_done`.

- [ ] **Step 3: Give the model the problem.** In `check_act`:

```python
for words in unbacked_actions(call.text, done):
    errors.append(f"the reply says '{words}', but no tool call does it. Nothing runs after your reply. Call the "
                  "tool in this reply, or change the text")
```

Add to `ACT_SYSTEM`: "Never promise an action for later. Do it now with a tool, or say that you cannot do it."

- [ ] **Step 4: Run the tests.** All pass.

- [ ] **Step 5: Commit.**

---

### Task 3: Ask before removing several picks

When `remove_picks` has 2 or more picks, Python asks first, unless the shopper gave the number of each pick or said "all". This replaces the trust in `one_pick`, which Nemotron and Luna both set wrong.

**Files:** `src/scout/agent/followup.py`, `tests/test_followup.py`, `eval/chat/conversations.yaml`.

- [ ] **Step 1: Write the failing tests.**

| Shopper's message | Picks in the call | Python |
|---|---|---|
| "Remove the red one" | 2, 4 | asks "all of these or one of them" |
| "Remove the Bomann" | 1, 3, 5 | asks |
| "Remove 2 and 4" | 2, 4 | removes |
| "Remove both red ones" | 2, 4 | removes |
| "去掉所有红色的" | 2, 4 | removes |
| "Remove the red one" | 2 | removes |

- [ ] **Step 2: Change `unclear_question`.**

```python
ALL_WORDS = re.compile(r"\b(all|both|every|each|these|those|them)\b|全部|所有|都|两个|三个", re.I)


def unclear_question(remove: RemovePicks, picks: list[dict], last_user: str) -> str | None:
    if not remove.picks:
        return "Which pick do you want to remove? Give its number."
    if len(remove.picks) < 2:
        return None
    named = {int(n) for n in re.findall(r"\d+", last_user)}
    if set(remove.picks) <= named or ALL_WORDS.search(last_user):
        return None
    options = ", ".join(f"pick {n} ({picks[n - 1]['title'][:50]})" for n in remove.picks)
    return f"Do you mean all of these: {options}? Or only one of them? Say all, or give the number."
```

When the shopper answers "all", the model calls `remove_picks` again, and `ALL_WORDS` lets it pass. Remove the field `one_pick` from `RemovePicks`.

- [ ] **Step 3: Change the expected values.** In `conversations.yaml`, the scripts `en-unclear-brand` and `zh-unclear-red` now expect a question and no removal, as the other unclear scripts do.

- [ ] **Step 4: Run the tests.** All pass.

- [ ] **Step 5: Commit.**

---

### Task 4: The model calculates by itself

**Files:** `src/scout/agent/followup.py`, `src/scout/agent/graph.py`, `tests/test_followup.py`, `tests/test_graph.py`, the spec and `CLAUDE.md` (after Zheyun agrees).

- [ ] **Step 1: Write the failing tests.** A reply "Pick 1 with shipping costs 6147.51 SEK" with a claim for the price of pick 1 and a derived number 6147.51 passes. The same reply without the derived number gives the problem "the number 6147.51 ...". A wrong claim (price 5000 when the card says 5648.51) still gives a problem.

- [ ] **Step 2: Add derived numbers to `Reply`.**

```python
class Derived(BaseModel):
    value: str
    how: str = Field(description="How you got it, in words, for example 'pick 1 price plus the 499 SEK shipping "
                                 "that the shopper gave'")


class Reply(BaseModel):
    """Your message to the shopper."""
    text: str
    claims: list[AnswerClaim] = Field(default_factory=list)
    derived: list[Derived] = Field(default_factory=list)
```

In `check_reply`, the numbers of `derived` are allowed in the text. Python does not check the arithmetic. The grader does (Task 5). A number of a fact (a size, a rating) must still be a claim, not a derived number. The grader also checks this, because Python cannot tell a fact from a result.

- [ ] **Step 3: Change the prompt.** Replace the number paragraph of `ACT_SYSTEM` with:

"You can calculate sums, differences, conversions and comparisons. Use only the facts on the cards and the costs and rates that the shopper wrote. Never add shipping, duty, moms or an exchange rate that the shopper did not give. Ask for it. Every number in the reply text must be a claim (a fact of a card), a number that the shopper wrote, a pick number, or a derived number with how you got it."

- [ ] **Step 4: Remove the `calculate` step** (decision 1). Remove `calculate` from `NextStep` and `STEP_ACTIONS`, and the `calculate` node and edge from the graph. Change the calculation scripts in `conversations.yaml` to expect the route `answer`.

- [ ] **Step 5: Change the spec and `CLAUDE.md`** as written under "Spec changes".

- [ ] **Step 6: Run the tests.** All pass.

- [ ] **Step 7: Commit.**

---

### Task 5: The grader

The grader is a model of another company than the chat model. It reads one turn and scores the reply on each criterion of the rubric. It writes the reason before the result, so it thinks before it decides.

**Files:** `src/scout/eval/grade.py`, `src/scout/eval/chat.py`, `src/scout/llm/client.py`, `src/scout/cli.py`, `config/models.yaml`, `tests/test_grade.py`.

- [ ] **Step 1: Write the rubric.** Each criterion is `pass`, `fail` or `not_applicable`.

| Criterion | Pass when |
|---|---|
| `answers` | The reply answers what the shopper asked, or asks one clear question when the message is not clear |
| `facts` | Each fact about a product agrees with the cards. No fact comes from outside the cards |
| `arithmetic` | Each calculated number is correct, and uses only the cards and the costs and rates that the shopper gave |
| `said_did` | The reply states or promises no action that the actions of this turn did not do |
| `honest` | The reply says when data is missing or old, and does not invent reviews, texts or stock |
| `plain` | The reply is short, clear and in English, with no text that the shopper did not need |

- [ ] **Step 2: Keep what the grader needs in each turn record.** `run_scripts` adds `cards` (the `card_text` lines that the chat model saw) and `actions` (the route: tools, removed picks, restored products, step).

- [ ] **Step 3: Write `grade.py`.**

```python
class CriterionGrade(BaseModel):
    name: Literal["answers", "facts", "arithmetic", "said_did", "honest", "plain"]
    reason: str
    result: Literal["pass", "fail", "not_applicable"]


class Grade(BaseModel):
    criteria: list[CriterionGrade]


def grade_turn(llm, record: dict, history: str) -> Grade: ...
def grade_results(llm, results: dict) -> dict: ...  # adds "grades" to each measured turn, and a summary
def agreement(labels: list[dict], grades: list[dict]) -> dict: ...  # per criterion: agreement, kappa, false pass
```

- [ ] **Step 4: Add the role and the command.** `config/models.yaml` gets the role `judge` (the model from Task 6). `Role` gets `"judge"`. `scout grade <results.json> [--judge provider:model]` grades a saved results file and writes `<name>-graded.json`. The chat run and the grading are separate, so one chat run can be graded by several judges.

- [ ] **Step 5: Tests** with `FakeLLM`: the grade is written for measured turns only, an outage turn is not graded, and `agreement` gives the right numbers for a small table.

- [ ] **Step 6: Commit.**

---

### Task 6: Zheyun's labels and the choice of the judge (Zheyun)

- [ ] **Step 1: `scout label <results.json>`.** For each measured turn, the command shows the cards, the conversation, the actions and the reply. Zheyun types `p`, `f` or `n` for each criterion, and a note when she wants. The labels go into `eval/labels/replies.yaml` (decision 2). She can stop and continue later.

- [ ] **Step 2: Zheyun labels 30 to 50 replies.** Claude chooses the turns so that each criterion has some failures: the turns with rejected rounds, the calculations, the unclear removals and the off-topic questions. Claude does not label.

- [ ] **Step 3: `scout calibrate --judge provider:model`.** Grades the labelled turns and prints, for each criterion, the agreement, Cohen's kappa and the false passes. Ask Zheyun before this run (about 1 USD for the four judges).

- [ ] **Step 4: Choose the judge** by decision 3, and put it in `config/models.yaml`. Write the table of the four judges into this plan.

- [ ] **Step 5: Commit.**

---

### Task 7: Run and grade (after Zheyun agrees)

- [ ] **Step 1:** Ask Zheyun. Then run all 24 scripts with Luna on the code of Tasks 1 to 4 (about 0.07 USD), and grade the results with the chosen judge.
- [ ] **Step 2:** Write the results into this plan: the expected values, the rounds that Python rejected and why, and the pass rate of each criterion. Compare with the run of 2026-10-05.
- [ ] **Step 3:** If the arithmetic pass rate is good, delete `agent/calc.py` and its tests (decision 1).
- [ ] **Step 4: Commit.**

## Not in this plan

- The off-topic question that started a search (fault 4). Task 1 shows the reason first. The `answers` criterion measures it.
- A dedicated judge model (for example the Prometheus family). OpenRouter does not have one. A later comparison on the same labels can be a CV point.
- Langfuse scores. Task 14 of phase 2 brings Langfuse. The grades can go there later.
