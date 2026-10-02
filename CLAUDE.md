# Incense product-selection agent

A LangGraph agent that helps Zheyun choose which incense products to test-sell in Sweden. It also serves as a CV project for AI Engineer roles, so it must be real, measured and deployable.

## Goal

Given a question such as "incense suited to winter gifts", the agent collects market signals, extracts them into a fixed schema, computes margins, ranks candidates and writes a report with a source link on every number. Zheyun makes the final choice. A small test batch of 5-10 products decides what actually sells; the agent only narrows the field.

## Planned graph

1. Plan: choose sources for the question.
2. Fetch in parallel: Google Trends (Sweden), Tradera, Etsy, Amazon.se, 1688/Taobao supplier prices.
3. Extract with an LLM into a fixed schema: product name, price, rating, review count, main complaints, source URL.
4. Compute landed cost and margin in Python, never with the LLM: supplier price + shipping + customs duty + 25% moms + platform fee.
5. Score and report, every figure linked to its source.
6. Human decision (human in the loop).

## Evaluation

- Extracted prices match the source page.
- Margin calculations are correct.
- Every claim in the report carries a real source.

"Did it pick the right product" has no ground truth and is not a metric.

## Engineering

- Tracing with LangSmith or Langfuse; record cost and latency per run.
- FastAPI + Docker once the core works.
- Optionally expose the tools as an MCP server.
- Check each platform's terms before scraping. Prefer official APIs and existing libraries.

## Rules

- Write plainly. No em dashes or colons in anything Zheyun will send or sign.
- Report real numbers only. Nothing goes into her CV until she confirms it.
