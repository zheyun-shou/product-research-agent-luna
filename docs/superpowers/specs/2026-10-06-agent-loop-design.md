# Agent loop design

Date: 2026-10-06. Status: accepted by Zheyun on 2026-10-06, with her answers in section 13.
Extends: `2026-10-02-product-scout-design.md` (sections 1a, 3 and 4.8 change when this design is accepted).

## 1. Why

Today the chat model (GPT-6 Luna) works in fixed steps. Python decides when to search, what to fetch, which candidates stay and how they rank. In each step the model gets a narrow prompt and little context. For example, the step that writes the picks sees only the cards. It does not see the conversation, and it can write only one or two sentences for each pick.

A test on 2026-10-06 showed the cost of this. Luna saw a fridge with a height of 1 cm (wrong seller data). It wrote "the unusually low height value may be a data issue", but it could not take the fridge out of the picks, because Python had already ranked it as a match.

Zheyun wants Luna to be an intelligent assistant with the whole context. It calls tools freely, searches the web, reads the results and makes the overall judgement. Python must not hold it in tight limits.

## 2. The principle

**Luna decides. Python keeps the facts true and the fetching polite.**

| Luna decides | Python guarantees |
|---|---|
| What to search, on which site, with which words | Each fetch is polite: random gaps, page limits for each turn, the login and captcha pause |
| How many pages to read, above the floors (section 5) | The floors of section 5 are met before the picks show |
| Which pages and reviews to read | Each extracted value has raw text that is in its source |
| Which data is doubtful, and what to do about it | Each number in a reply traces to a record, a review, the shopper's words, or a calculation that the reply states |
| Which candidates fit, how they rank, how many picks to show | Each cited id (pick, listing, chunk, review) exists and belongs to what it is cited for |
| When to ask the shopper, and when to give picks; what to say, in the shopper's language | Tool calls are valid (the product exists, it was removed before it is restored) |

Python does not judge words or common sense (see the rule "the model judges the words").

## 3. What stays

- **LangGraph.** The graph changes shape. It does not go away. LangGraph has the agent loop as a standard pattern (a model node and a tool node with a conditional edge).
- **Postgres checkpoints.** The shopper can stop and continue a conversation.
- **The login pause.** A tool that meets a login page or a captcha pauses. The web page shows Done or Skip, as now.
- **Streaming.** The web page and `scout chat` show each reply and each tool report when it is ready.
- **The source adapters, the parsers and the stored data.** They become the inside of the tools.
- **The checks.** The raw-text check, the number checks and the citation checks stay. They move to the tools and to the final reply.
- **Langfuse traces.** Each model call and each tool call is a span.

## 4. The graph

```
           ┌──────────────────────────────┐
message →  │ compact (summary of old turns)│
           └──────────────┬───────────────┘
                          ▼
                 ┌─────────────────┐   tool calls   ┌──────────────────────┐
                 │ agent (Luna)    │ ─────────────► │ tools (Python)       │
                 │ system prompt + │ ◄───────────── │ run, check, report   │
                 │ global context  │   results      │ login pause inside   │
                 └────────┬────────┘                └──────────────────────┘
                          │ reply or show_picks
                          ▼
                 ┌─────────────────┐  problems: back to the agent, up to 3 rounds
                 │ final checks    │ ───────────────────────────────────────────►
                 └────────┬────────┘
                          ▼
                   reply to the shopper, state saved
```

- **agent.** One Luna call. It gets the system prompt (section 6), the conversation, and the results of the tool calls of this turn. It answers with tool calls, a reply, or both.
- **tools.** Python runs the calls. Calls that do not depend on each other run in parallel (for example several web pages). Each tool gives a short report for Luna and, when useful, a status line for the shopper.
- **final checks.** The checks of section 7 on the reply or on `show_picks`. Problems go back to Luna with the values from the records, as now.
- **Limits for each turn.** The first values are 25 tool calls, 30 product pages, 30 review pages and 20 web pages. At a limit, the tool says so, and Luna replies with what it has. The values are set again from the measurement of section 10 (the hidden-target test).

## 5. The floors (decided by Zheyun on 2026-10-06)

Luna can always do more. It cannot do less than this. Each floor is kept inside a tool where possible, and else by the check before the picks.

| Floor | How Python keeps it |
|---|---|
| At least one result page for each search | `search_shop` always reads page 1 |
| When the site has a sort by rating or by sales, use it too | `search_shop` page 1 also reads page 1 sorted by rating (`s=review-rank`) and by sales (`s=exact-aware-popularity-rank`). The sort menu of amazon.se, read on 2026-10-06: Featured, price up, price down, average customer review, newest arrivals, best sellers |
| Search the web for tests by default | Before `show_picks`: each pick must have a web search for its model, in this conversation or stored from the last 7 days. The shopper can switch this off in the conversation |
| Read good and bad reviews | `read_reviews` always reads the positive and the critical pages (as in Task 5) |

When the check before `show_picks` finds a missing floor, it tells Luna what is missing, for example "pick 3 has no web search yet". Luna then does it and calls `show_picks` again.

## 6. The system prompt

One system prompt for Luna in all turns. In outline:

- **Who it is.** "You are product-scout, a shopping research assistant. You help the shopper choose products. The shopper is in Sweden. You find candidates in shops and on the web, check them against the shopper's needs and give a few picks with the reasons."
- **The global context, in each turn.** The needs so far, the summary of older turns, the current picks with their cards, the removed products, the searches already made in this conversation, and the page limits that are left.
- **How to work.** Look things up before you guess. Use several sources. Say clearly when data is missing, doubtful or in conflict, and what you did about it. You can set a candidate aside when its data cannot be right; say why. Ask the shopper when the needs are not clear enough to search.
- **Product rules.** A budget is a hard limit. Price does not rank the picks by default. When the shopper asks for it (for example "good value for money"), Luna can weigh the price (decided 2026-10-06). No shipping, duty or moms unless the shopper gives them. A currency conversion states its rate. Reviews and tests are summarized in qualitative words, never as counts.
- **The floors** of section 5, so that Luna plans for them.
- **Facts.** Each number comes from a tool result, the shopper, or your own calculation that you state. Cite the ids that the tools give.
- **Language.** Reply in the shopper's language.

## 7. Tools

Most tools exist already as code. The table gives what changes.

| Tool | What it does | Python inside | Exists as |
|---|---|---|---|
| `update_needs(needs)` | Records the needs: kind, conditions, budget, preferences | Each number is one that the shopper wrote (`check_needs`) | the clarify step |
| `search_shop(site, words, page, sort)` | Searches a shop, drops titles off the topic (worker), opens up to 8 new product pages, stores them. The kept results past the 8 stay in memory; the next call with the same words reads them without the search pages | Polite fetching, the floors of section 5, the raw-text check, the login pause | `crawl` |
| `search_web(question)` | Tavily search, reads the found pages in parallel | Stores test and forum text as chunks; shop pages go through the generic reader | Task 9 |
| `read_page(url)` | Reads one product page of any shop | schema.org JSON-LD first, then the worker with the raw-text check | new (generic reader of section 1a of the main design) |
| `query_products(conditions)` | Lists stored candidates that meet conditions, with their cards | SQL and Python filter; candidates over the budget are not given; the result is a list for Luna, not a decision | `query_products`, `shortlist` |
| `find_missing(products, attributes)` | Looks for missing values in the stored text | Raw text in the cited chunk, the unit check | Task 6 |
| `read_reviews(products)` | Reads the positive and the critical review pages | Polite fetching, page limits, the login pause | Task 5 |
| `summarize_reviews(products)` | Qualitative pros and complaints with cited reviews | Cited ids belong to the product; numbers are in the cited reviews | Task 7 |
| `read_product`, `search_text`, `read_images` | Look up text and images of stored products | as now | exist |
| `check_now(products)` | Opens the product pages again for the price, the stock and the sizes now | Reports a product that is sold out or now over the budget | the recheck in the picks |
| `remove_picks`, `restore_picks` | The shopper rejects or wants back products | as now | exist |
| `show_picks(picks)` | The final picks: for each, the listing id, the reasons, the claims, the doubts | Runs `check_now` on each pick that was not checked in this turn. A pick that is sold out or over the budget goes back to Luna. Then the checks of section 8 | the picks writer |

`show_picks` replaces the fixed picks step. Luna chooses the picks and their order. It can show fewer than 5 when fewer fit well, and says why (decided 2026-10-06).

## 8. Checks before the picks and the replies show

Zheyun decided on 2026-10-06 to keep only three checks at this gate. The budget and the live check of the price moved into the tools (`query_products`, `check_now`, `show_picks`).

1. **Numbers have a source.** Each number in a reply or in the picks is a value of a record, a number that the shopper wrote, a number in a cited review, or a calculation that the reply states. The claims cite the card field, or the stored page text of the product (specifications, bullets, description; not the reviews on the page). The page text was added after the first real turn (section 14).
2. **Citations exist.** Each cited listing, review or chunk exists and belongs to the product that it is cited for.
3. **The floors of section 5** are met before `show_picks`.

A problem goes back to Luna with the values from the records, up to 3 rounds.

**Removed: the word rules for actions** (decided 2026-10-06). Python no longer reads the reply for words such as "removed" or "I will search" to require a tool call. In the loop, Luna can call the tool in the same turn.

## 9. What changes for the shopper

- Luna can say "the seller gives 10 mm as the height of this 173 l fridge, which cannot be right, so I left it out" or "I could not confirm the height of pick 3".
- Luna can search more when the first results are weak, search the web for a model that the buyers praise, or ask a question in the middle of a search.
- A turn can take longer and cost more, because Luna makes more calls. The limits of section 4 cap this.

## 10. Evaluation

- **Hidden-target test** (asked by Zheyun on 2026-10-06). Claude selects real products that are on amazon.se, for example 20 across the categories. For each, Claude writes the needs of a shopper from the facts of the product, without its name, brand or model. A simulated shopper (the planned Task 14) knows the hidden product and answers Luna's questions only from its facts. We measure: is the hidden product in the picks, at which place, how many tool calls, product pages, review pages and web pages, how many model calls, the cost and the time. The limits of section 4 are set from these numbers (for example from the run that needed the most, plus a margin). This is the idea of the Princeton WebShop benchmark (Yao et al., 2022), which Zheyun had in mind.
- `scout eval-chat` scripts check routes today (for example "remove pick 2"). In the loop there are no fixed routes. The scripts change to check outcomes: which picks are removed, which tools were called, what the reply must or must not say, and that the checks pass.
- Compare the fixed pipeline (the code now, kept on a branch) with the loop on the same scripts: the share of turns that pass the checks in the first round, facts correct (grader), floors met, number of model calls, cost and time for each turn.
- Later the baseline of the main design: Claude with web search on the same needs.
- These runs cost money. Each waits for the OK of Zheyun.

## 11. Steps

1. Check the sort orders of amazon.se (one search page for each, with her browser).
2. The system prompt and the global context. The agent and tools nodes in LangGraph, with the existing lookup tools and `remove_picks` and `restore_picks`.
3. `update_needs`, `search_shop`, `query_products`, `check_now`, `show_picks` with the checks of section 8. The fixed pipeline goes away.
4. `read_reviews`, `find_missing`, `summarize_reviews` as tools, with the floors.
5. The web page and `scout chat` on the new graph: streaming of tool reports and the login pause.
6. eval-chat scripts for outcomes, the hidden-target test, and the comparison of section 10 (paid, with her OK). Then set the limits of section 4 from the numbers.
7. `search_web` and `read_page` when the Tavily key is there (Task 9).

Each step is committed, reviewed and pushed. The tests use a fake LLM that returns planned tool calls.

## 12. Risks

| Risk | What we do |
|---|---|
| More calls, more cost and time | The limits of section 4. Langfuse shows the cost of each turn. Cheap work (title screening, extraction) stays with the worker model inside the tools |
| Luna loops or stops too early | The tool-call limit. The floors before `show_picks`. Eval scripts that measure it |
| Behaviour varies between runs | Outcome checks in eval, several runs for each script |
| The free fallback model (Gemini) uses tools less well | Measure it in the comparison. The fallback can stay on a simpler path |
| The login pause in the middle of a tool | It works as now: the tool waits for Done or Skip. Skip ends that tool with a report to Luna |

## 13. Decisions of Zheyun (2026-10-06)

1. Price: Luna can weigh the price when the shopper asks for it. A budget stays a hard limit.
2. The word rules for actions go.
3. The limits for each turn: start with the values of section 4, then set them from the hidden-target test.
4. Luna can show fewer than 5 picks when fewer fit well, and says why.
5. The gate before a reply keeps only three checks: numbers have a source, citations exist, the floors.

## 14. The first real turn (2026-10-06)

One real conversation: "a fridge with a freezer, at most 180 cm high, budget 6000 SEK". Results:

- The first turn took 5353 seconds and gave no picks. It used all 25 tool calls and all 30 product pages. The gate sent the picks back three times.
- Luna worked well in parts. When a search gave no results, she used other Swedish words (6 searches). She looked for missing values, read the good and the bad reviews, and wrote qualitative summaries. The floor sent back a pick whose reviews were not read, and she read them.
- The fetch log shows 77 pages in 199 seconds of loading. 14 pages gave HTTP 503 and loaded again on a retry. No captcha came.
- Two gaps without a fetch took 35 and 41 minutes. Both were inside one search, between two product pages, where the worker model extracts the page. The HTTP timeout of 120 seconds counts only the time without data. The probable cause is a provider that keeps the request open with keep-alive bytes. This is not proven.
- The gate rejected numbers that Luna read in the page text (for example a height of 142.5 cm), because the claims could cite only card fields. Two errors were real: a price with wrong decimals and a rounded review count.
- Luna read the same products again and again. The cause: a tool report was cut at 6000 characters, and the text of three products is longer. So she did not see the second and the third product.

Changes after this turn:

- Each model call has a deadline in all: 180 seconds for chat, 60 for worker, 120 for judge (`deadline` in models.yaml can change it). A late call is tried one more time, then it raises `LLMTimeout`. It does not move the role to its fallback, and the crawl stores that page without extraction. The late request cannot be stopped; its thread ends by itself.
- The crawl extracts pages in 4 worker threads while the browser loads the next pages.
- `search_shop` opens up to 8 new product pages. The sort orders take turns in the list, so the best rated and the best selling results are in the first 8. The rest stay for the next call with the same words.
- The polite gap between pages is now 2.5 to 3.5 seconds (Zheyun, 2026-10-06), not 4 to 9.
- A claim can cite the page text of its product (field `page`), and a number of the page text is a source for the text of that pick. Prices in the page text ("299 kr") are not a source, and the price, the rating, the review count and the stock come only from the card.
- `read_product` cuts each product at 5000 characters, and its report keeps all three. A product read in this turn is not given again.
- The system prompt tells Luna to look at the stored products first.
- The limit of 30 product pages for each turn stays.

Goal from Zheyun: 3 to 4 minutes for a turn. A new category needs about 20 pages (3 search pages, 8 product pages, 9 review pages), so it is not sure that this goal can be met there. The next real turn measures it.

