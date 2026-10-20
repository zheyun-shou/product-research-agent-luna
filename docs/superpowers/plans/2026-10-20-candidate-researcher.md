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
