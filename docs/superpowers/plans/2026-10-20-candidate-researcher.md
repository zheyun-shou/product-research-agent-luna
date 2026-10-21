# The candidate researcher (a subagent)

The main agent (Haiku 5.5) finds candidates and decides the picks. Then the research of each candidate fills its context: the page text, the reviews, the web tests and the missing values. A turn often uses all 25 tool calls and 30 product pages. So the research of each candidate goes to a subagent with its own context, and the main agent gets a short file for each one.

This is the orchestrator and worker pattern (Anthropic, "How we built our multi-agent research system", 2025; open_deep_research). Only this part of the work becomes a subagent. The shop search and the choice of candidates stay with the main agent, because their steps depend on each other.

## Design (2026-10-20)

- A new tool of the main agent, `research_candidates(ids, want)`, for at most 5 products.
- First Python reads the amazon.se reviews of the products that were not read in the last 7 days. The browser works in one thread only, so this step is not parallel. It counts for the reviews floor, as read_reviews does.
- Then one researcher for each product starts, all at the same time. Each one is a small tool loop with its own context: the needs of the shopper, the card of its product, and the wish of the main agent.
- The tools of a researcher work only on its own product: read_product, search_text, find_missing, summarize_reviews, search_web and think. Python sets the product id in each call. A researcher cannot use the browser, change the needs or show picks.
- A researcher has at most 8 model calls. In the last one it can only finish. Each one gets an equal part of the web searches and the web pages that are left in the turn.
- It finishes with a file: whether the product fits the needs (fits, does not fit, unclear) and why, at most 10 points (a need, a pro, a con, a test or other), each with its source and a quote, and what is still missing.
- Python keeps a point only when its quote is in a tool result that this researcher got. This is the same rule as for the extracted values and the web points: the raw text must be in what the model read. The main agent sees how many points were left out.
- The researchers share one database connection. The tools that use the database run one at a time (a lock). The model calls of the researchers and their web searches run at the same time.
- The researchers use the model of the role `researcher` in `config/models.yaml`. Without that role, they use the worker. The first setup uses the worker (Gemma 4 26B on OpenRouter), because it is cheap. The evaluation shows if it is good enough, or if the role needs Haiku.
- The web floor is met when the researcher made a web search for its product. The main agent sees the files, not the tool results of the researchers. The evaluation keeps the transcript of each researcher, so I can check the files against the sources.

## Measure

eval-hidden with and without the tool, on the same targets: the hit rate, the cost, the time and the tool calls of the main agent. Paid, so it waits for my OK. The claim for the CV comes only from this run.

## Not in this plan

- Subagents for the shop search.
- A researcher that uses the browser (amazon.se pages, read_page). The browser works in one thread only.

## Progress (2026-10-20)

- Built: the tool `research_candidates` (agent/loop.py) and the researcher (agent/researcher.py), the role `researcher` in `config/models.yaml` (Gemma 4 26B, as the worker), and the step texts in three languages.
- The prompt of the main agent says to call research_candidates when it has the candidates, and to read more itself only where a file says that something is unclear. research_products and search_web stay.
- The main agent counts the research as one tool call. `used` keeps the steps of the researchers (`researcher_steps`) and the transcript of each researcher with the kept and dropped points (`researchers`), so eval-hidden saves them.
- The reranker of the web search runs for one researcher at a time (a lock).
- Tests with fakes: each researcher works only on its own product, Python reads the reviews before the researchers start, a point with a quote that is not in the tool results is left out, one failed researcher does not lose the files of the others, and in the last step a researcher can only finish. Not run with a real model yet.

### Code review (2026-10-20)

The review found 10 problems. 9 are fixed, and 1 is a known limit.

- One lock for the database connection (`LoopDeps.db_lock`). The researchers share one connection, and it is not in autocommit, so all database work takes this lock. search_web takes it only for its database parts, so a researcher can search the web while another one reads the database.
- Known limit: summarize_reviews and find_missing hold the lock during their worker calls. So these two tools of different researchers run one after the other. A pool of connections would fix this. I add it only if the evaluation shows that the research is too slow.
- A point cannot quote the needs or the wish of the main agent. It can quote the stored facts of the product and the tool results.
- The reports of the researchers have the same length limits as for the main agent (search_web up to 60,000 characters).
- When the web search is not available for a researcher, the turn knows it, so the web floor does not ask again.
- A failed review read does not stop the research. The main agent gets the full review report.
- A researcher that fails keeps its counts of web searches, pages and credits for the turn.

### Smoke run and fixes (2026-10-21)

One eval-hidden run with the researchers (red-bottom-freezer, Chinese): the target was pick 1, 2 shopper turns, 0.056 USD, 16 minutes, 21 researcher steps. Three problems:

- The researcher of the target said "does not fit" because buyers said that it is loud. The shopper did not ask about noise. Another researcher said "fits" for a fridge with the freezer at the top. Now the prompt says that the fit is only about the recorded needs and the budget. A problem that is not a need is a con point.
- The researcher of the target made no web search, so the web floor stopped the picks one time. Now the prompt says to always search for tests. Also, after the researchers, Python makes one web search ("brand model test") for each product that has no web search for tests yet.
- Five researchers searched at the same time. The DuckDuckGo and Brave engines of SearXNG asked for a captcha, and the search went to Tavily (6 credits of the free plan). Now at most 2 web searches of all researchers run at the same time.

Second smoke run (same target, after the fixes): the target was pick 1, 1 shopper turn after the opening, 0.028 USD, 10 minutes, 18 researcher steps, 5 web searches (1 with sources tests), 3 Tavily credits, 0 gate stops. Four researchers said "fits" and one said "unclear", each for the recorded needs. Two said that they could not check the budget, because the price was not in their context. Now the product line of a researcher has the price. DuckDuckGo still asked for a captcha with 2 searches at the same time.

Third smoke run (Haiku 5.5 as the researcher): the target was pick 1, 0.079 USD, 12 minutes, 16 main tool calls, 30 researcher steps, 2 gate stops. The fit of each researcher was right for the recorded needs. But the 5 researchers of the first research used all 10 web searches and all 120 web pages of the turn: they searched with sources tests first, found nothing, and searched again with any. So the 3 later candidates had no web search, and only 1 pick was shown. Changes:

- The researchers together get half of the web searches and pages that are left, at least 1 search each. The other half stays for the main agent.
- The prompt says to search with sources any first. The other sources are only for a search that is left.
- Python does not try its own web search when the searches of the turn are used up.

### Connection pool (2026-10-21)

The known limit of the first review is fixed. The loop now uses `ThreadConnections` (db/conn.py): the main thread keeps its connection, and each researcher takes a connection of a small pool (psycopg_pool, at most 5) while it works. So summarize_reviews and find_missing of different researchers run at the same time. Before the researchers start, the main connection closes its open transaction, because its row locks could stop a researcher while the main thread waits. When a researcher ends, its transaction closes, so the main thread sees what it stored. The embedder and the indexing of search_text still run one at a time (a lock in `hybrid_search`). Without a pool (the tests with fakes), the researchers share one connection with `db_lock`, as before.

### Code review of the pool, Reddit, the fetch log and the list of the chats (2026-10-21)

The review found 10 problems. All are fixed.

- The pool was never closed, and eval-hidden made a new one for each target. So about 20 targets could use all connections of Postgres. Now each command (chat, web, eval-hidden) opens one `ThreadConnections` and closes its pool at the end.
- A researcher commits after each tool, or rolls back a failed statement. Before, one failed statement stopped all later tools of that researcher, an error rolled back what it stored, and its row locks stayed while it waited for its model. Short transactions also make a deadlock between two researchers rare. If one happens, Postgres stops one statement and the researcher goes on.
- In `hybrid_search` only the load of the embedder, the indexing and the calls of the embedder run one at a time (its tokenizer is not safe in threads). The database queries of the researchers run in parallel.
- An error of the Python web search after the researchers no longer loses their files. Without a model, Python searches with the title, not the brand alone, because "Samsung test" would meet the web floor with tests of other products. When the search was not made, the line says so and does not tell the chat model to look for stored pages.
- An error of the fetch log no longer loses a Reddit thread that was read.
- The list of the chats does not take the lock of the turns, so it cannot make a message fail as busy. A turn waits up to 2 seconds for a short read. A thread with no message yet is not kept without a title.
