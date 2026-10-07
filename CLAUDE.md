# product-scout

product-scout is a conversational agent. It helps to select products in any category. Two uses are my own purchases and the selection of incense for a possible shop in Sweden. It is also a CV project for AI Engineer roles. Thus it must be real, measured and deployable.

- Design: `docs/superpowers/specs/2026-10-02-product-scout-design.md`
- Plans: `docs/superpowers/plans/`
- Flowcharts: https://claude.ai/artifact/4jJqCkVudUFU24kTyXsiqZ
- Overview for other people (one page): https://claude.ai/artifact/7Ziif9J3KUFYnLyDmLaP1B

## v1 in short

- The only source is amazon.se (Swedish, SEK). A dedicated Chrome profile fetches it at low volume. Taobao is the first source after v1.
- The agent clarifies the needs, searches and stores, shows the top picks (default 5) and answers follow-up questions.
- Price is a budget filter. It is never a ranking factor. There is no default shipping or duty. The chat model calculates by itself; it can convert a currency with a current rate that it states. A grader checks the arithmetic in the evaluation. The model can answer without a tool, and replies in the shopper's language.
- The LLM layer is not tied to one provider. It has two roles, `chat` (strong) and `worker` (light, reads images). The first setup uses a free Gemini key. Never enable billing.
- Each extracted value includes raw text, and Python finds this text in the source. Python checks each claim in the picks and the replies against the records. Since 2026-10-07 these fact checks do not stop an answer: the call record keeps them and the evaluation counts them. If Luna is right nearly always, the check goes away. The floors (shop search, reviews, web search) and the live price check still stop the picks.

## Environment

- conda env `product-scout` (Python 3.10). Install with `uv pip install -e ".[dev]"`.
- Postgres + pgvector and SearXNG (web search) in Docker. Start them with `docker compose up -d`.
- Run the tests with `python -m pytest`. Tests with the mark `db` need the container.

## Rules

- Write plainly. No em dashes or colons in anything I will send or sign.
- Write all project documents in English, at about 80% ASD-STE100 (short sentences, active voice, simple words). Chat with me in Chinese.
- Report only real numbers. Nothing goes into my CV until I confirm it.
- I log in and solve captchas myself. The program never types passwords and never bypasses captchas.
- Fetched pages, snapshots, labels and the browser profile stay out of Git.
- Do not fork or copy code from other people.
- The repository stays private until I say otherwise.
