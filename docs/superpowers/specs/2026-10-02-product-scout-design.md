# product-scout design

Date: 2026-10-02, revised 2026-10-03
Status: awaiting review

Flowcharts: https://claude.ai/artifact/4jJqCkVudUFU24kTyXsiqZ

## 1. Goal

A conversational agent that helps pick products in any category. A buyer choosing a fridge and a seller choosing which incense to stock are both use cases.

Two purposes, equally important.
- Practical. Zheyun really uses it for her own purchases and for incense selection.
- CV. A project for AI Engineer job applications, so it must be real, measured and deployable.

The system narrows the field. The user makes the final choice.

## 2. Scope

### In v1

- Four test categories: fridge, incense, headphones or small electronics, clothes or shoes.
- Clarify the user's needs, search, give a few top picks, then answer unlimited follow-ups or suggest alternatives. Conversations can be paused and resumed.
- Product sources: Amazon, Taobao, JD, Swedish shops (Prisjakt, Elgiganten, Tradera, EPREL and others).
- Review sources: shop reviews, Xiaohongshu, Zhihu, Reddit, Flashback, professional review sites (Råd & Rön, Rtings, SweClockers and others).
- Text retrieval is hybrid: vector search plus BM25.

### Not in v1

- Video reviews (YouTube, Bilibili). Later. The hard part is getting transcripts, not cost.
- Default currency conversion, shipping, duty or moms. The system calculates only when the user says how.
- Listing eliminated products in the picks. Eliminations are stored in the database only.
- Tagging reviews with complaint types. Add later if counts are needed.
- Letting the LLM drive a browser through MCP. Code fetches pages in fixed steps; the LLM only reads cleaned content.
- Copying or forking other people's code. Other projects are read for ideas only (for example MediaCrawler's approach to crawling with a logged-in browser).

## 3. Architecture

Built with LangGraph. Each product search is one conversation thread, with state stored in Postgres. There are four phases; follow-ups lead back into earlier phases.

```
User: I want a fridge, my requirements are ...
   ↓
1. Clarify needs
   Load the category profile (if none exists, the LLM drafts one, it is saved, the user can edit it).
   The LLM compares the user's message with the profile. If needs are unclear, ask, until they are clear.
   ↓
2. Search and store
   Before starting, check login for every browser-based site this search needs; ask the user to log in up front.
   a. Search product sites with the user's filters; the result lists give rating and sales per candidate.
   b. Search the profile's review sources at category level; count how often each model is mentioned.
   c. Fetch detail pages in Threshold Algorithm order over the three lists (see 4.3) and store them (see 4.4).
      Stop at 50 detail pages or when no unread candidate can reach the top k.
   d. For the top 10 candidates, fetch model-specific reviews (shop reviews and review sources), up to 30 pages.
   Every fetched page is also checked for login and captcha.
   ↓
3. Recommend
   SQL filter plus Python scoring; take the top picks (default 5).
   If candidates are priced in different currencies and price affects the ranking, ask the user first (see 4.7).
   Recheck every pick for price and availability; replace sold-out picks with the next candidate.
   The LLM writes the picks citing record IDs; Python checks the IDs and numbers.
   ↓
4. Follow-up
   Question about a pick → retrieve and answer
   Calculation           → Python computes and shows the formula
   Not happy             → exclude rejected picks, rerank, back to phase 3
                           if too few candidates remain, back to phase 2 to search more
```

## 4. Components

### 4.1 Category profile

One YAML file per category, describing what to ask, which conditions are common and where to look. A profile does not list every attribute of the category. Attributes it leaves out are extracted when needed (see 4.4).

```yaml
category: fridge
display_name: Fridge
attributes:            # common conditions and preferences
  - name: height
    label: Height
    type: number
    unit: cm
    kind: hard        # hard condition, used to eliminate
  - name: noise
    label: Noise
    type: number
    unit: dB
    kind: soft        # preference, used to rank
questions_for_user:
  - How wide, tall and deep is the space for the fridge?
  - What is your budget?
  - Do you care about noise?
complaint_types: [noise, frost, door seal, compressor life, after-sales service]
sources:
  products: [prisjakt, elgiganten, eprel]
  reviews: [prisjakt_reviews, rad_och_ron, flashback, xiaohongshu]
```

- Attribute types: number with unit, enum, boolean, text.
- `complaint_types` is not used to tag reviews. It guides the picks: when summarising each product's complaints, retrieval runs once per type.
- **Preset profiles**: the four test categories plus about 10 common categories. Their review sources come from one-off research and are written into the profile.
- **New categories**: the LLM drafts a profile and searches for review sources for that category. Sources with an adapter are used directly, public pages go through a generic fetcher, and sources that need login or have strong anti-bot measures are listed as "suggested new adapters".
- The user can edit a profile. Edited versions are saved and reused.
- If many products show the same attribute that the profile lacks, ask the user whether to add it to the profile.
- After each use, record how many matched reviews each source contributed. Sources that contribute little move down the list next time.

### 4.2 Source adapters

Product sources and review sources share one interface, with one adapter per site.

```python
class SourceAdapter(Protocol):
    name: str
    kind: Literal["product", "review"]
    access: Literal["official_api", "user_browser", "public_page"]
    rate_limit: RateLimit

    def check_session(self) -> SessionStatus: ...   # user_browser only
    def search(self, query: SearchQuery, profile: CategoryProfile) -> list[Candidate]: ...
    def fetch(self, url: str) -> RawPage: ...
    def parse(self, page: RawPage, profile: CategoryProfile) -> list[ProductRecord] | list[ReviewRecord]: ...
```

- `search` pushes hard conditions into the site's own filters where it can (predicate pushdown) and supports several sort orders, such as rating and sales.
- Access depends on the site. Sites with an official API use it (EPREL, Tradera). Taobao, JD, Amazon, Xiaohongshu and Zhihu use the user's own logged-in browser through a persistent Playwright profile, at low volume, much like the user browsing. Other public pages are requested directly.
- **Browser-based sites read the JSON the page itself requests.** On sites like Taobao, product data is loaded by separate API calls from the page. Playwright listens to network responses and gets ready-made fields. This is more accurate than parsing HTML and needs no LLM call. If that fails, read the rendered DOM.
- Each adapter records its terms-of-service check and the date. Adapters without a terms check stay disabled.

#### Login and captcha

- `check_session` decides whether the user is logged in. Any of these counts as logged out: a redirect to the login page, a login dialog on the page, or captured JSON reporting "not logged in" or "session expired". A captcha is handled the same way.
- Before phase 2, check every browser-based site this search needs, so the user is asked up front. Check again on every page, because a session can expire or a captcha can appear mid-search. The per-page check inspects the page just fetched and sends no extra request. Availability rechecks in phase 3 go through the same check.
- On a problem, pause with a LangGraph interrupt. Open the site's login page in the user's browser and tell the user in the chat, for example: "You are not logged in to Taobao. I opened the login page. Reply 'done' after logging in, or skip Taobao." After the reply, check again and resume where it stopped. If the user skips, continue with the other sites.
- The user always logs in and solves captchas. The program never types passwords and never bypasses captchas. Sessions are stored locally in `browser-profile/`, which is not in Git.

### 4.3 Search strategy

The goal is to avoid paging through endless results and to keep irrelevant products out. It draws on these classic methods.

1. **Predicate pushdown**: every hard condition the site can filter on goes into the site's search parameters.
2. **Top-k over several sorted lists (Fagin's Threshold Algorithm)**: three lists, all available before any detail page is fetched. Rating and sales come from the product sites' search results. Review mentions come from the category-level review search (phase 2b), matched to models as in 4.5. Read down the lists in parallel, fetching the detail page of each newly seen candidate. Stop when the best possible combined list score of any unread candidate is below the current k-th candidate (k = 10). The final ranking in phase 3 also uses detail-page data, so this stopping rule decides only which detail pages are worth fetching.
3. **Multi-stage ranking**: the first pass uses only the title, price, rating and sales shown in search results, to drop irrelevant products. Only candidates that pass get detail pages fetched, and only the top 10 after ranking get model-specific reviews and images.
4. **Budget**: each search has page caps. When one is used up, stop and say so in the picks. Defaults: 50 detail pages and 30 review pages. If a paid model is used, add a money cap, set after phase 1 measures the cost of one search.

### 4.4 Extraction and storage

Principle: **only information used for filtering and comparison is structured. Everything else stays as original text and is found by retrieval.** Each page is cleaned of scripts, menus and ads, then split into two paths.

**Structured path: a few fields only**

1. Read in order of reliability: captured JSON or structured data embedded in the page (JSON-LD), then the HTML text.
2. Core fields: name, brand, model, price, currency, rating, review count, in stock, source URL, fetch time. Also extract the attributes listed in the profile and any attribute the user's current conditions use, since they are needed for ranking anyway. Fields missing from structured data are extracted from the HTML by the LLM, in one call per page.
3. Validate with Pydantic.
4. Match on model number, brand and specs to see whether the product is already in the database. If it is, add or update the listing and variant. If not, create a product. Every fetched price also adds a price_history row.

Rule: **prices come only from structured data or HTML, never from images**, because prices in product images are often out of date.

**Text path: keep everything**

1. Product text, descriptions and review text are all chunked. A short review is one chunk. Long text is split by paragraph into chunks of about 300 to 500 tokens with a small overlap, each prefixed with the title and product name.
2. Images are processed only when key information is missing from the text and appears only in images. Try PaddleOCR first, then a vision model if OCR fails. Long images are cut into segments first. Text read from images only goes into the text path.
3. Each chunk gets an embedding and BM25 tokens (see 4.6) and is stored in `chunks`.

**On-demand attribute extraction**

When the user later adds a condition on an attribute that is not stored yet (for example "no taller than 180 cm"), retrieve the chunks about that attribute for each candidate, have the LLM extract the value, write it to `attributes` and record the source chunk ID. If nothing is found, mark it as missing and say so in the picks.

To keep the model from guessing, every extracted value must carry the raw text it came from (`raw`), and Python checks that this text really appears in the source page or chunk. Values that fail the check are dropped and marked missing.

Format of each value in `attributes`:

```json
{"height": {"value": 178, "unit": "cm", "raw": "1780mm", "source_chunk_id": 1234}}
```

Attributes outside the profile go in `attributes.extra`.

### 4.5 Reviews and product matching

Review fields: source URL, platform, author type (buyer, blogger, professional reviewer), publish date, matched product or "unmatched", original text, language, fetch time.

Community reviews often omit the model number, so they need matching to a specific product.
1. Exact match on model number when there is one
2. Otherwise a fuzzy match on brand, category and key specs
3. Finally the LLM judges whether it is the same product

Reviews that cannot be matched are not forced onto a product. They are filed under the brand or category as general reference.

### 4.6 Text retrieval

Product text chunks and review chunks share one retrieval path.

- **Vector search**: bge-m3, run locally, 1024 dimensions per chunk, stored in pgvector with an HNSW index. Chinese, Swedish and English share one vector space.
- **BM25**: implemented in Python by us. Tokenisation uses the bge-m3 tokenizer, so all three languages share one code path. A regex also adds each model number (for example `RB38C7B5D22`) as a single token, because subword tokenisation splits model numbers into pieces.
- **Merging**: the two result lists are merged with Reciprocal Rank Fusion (RRF). Each result scores the sum of `1/(60 + rank)` over the two lists.
- **Filters**: conditions such as product scope, author type and publish date apply to both searches. With strict filters, pgvector's HNSW can return too few results, so turn on the iterative scan added in pgvector 0.8.
- Each chunk records the embedding model version that produced it, so a model change shows which chunks need re-embedding.
- Behind the same retrieval interface there is a long-context implementation, used only as the evaluation baseline.

### 4.7 Picks

1. The user's needs and the category profile used
2. Comparison table: price, key specs and rating of the top picks (default 5), each cell linked to its source
3. Pros and complaints for each product, labelled by source type (professional test, buyer review, community user)
4. Missing and uncertain data
5. Fetch time and availability
6. How the ranking was done

**Which price to show.** A product with variants shows the price of the variant that fits the user's needs (size, color, capacity). If that is unknown, it shows the price range across variants. The ranking uses the lowest price of the fitting variants.

**Mixed currencies.** There is no default conversion (section 2). If the candidates are priced in more than one currency and price affects the ranking, ask the user before ranking: limit the search to one market, or give an exchange rate to use. The rate the user gives is shown in the picks with the formula.

**Before the picks are shown, every pick is rechecked for price and availability, however recently it was fetched.** A sold-out pick is replaced with the next candidate, which is then checked too. Candidates outside the top are not rechecked.

The picks are generated as structured output. Every claim carries the record IDs it cites. Python checks that the IDs exist and that numbers match the records. If not, the picks go back for a rewrite.

### 4.8 Follow-up tools

| Tool | Purpose |
|---|---|
| `query_products` | Query the product tables with structured conditions. The LLM never writes SQL |
| `search_text` | Text retrieval (4.6), to answer questions about a product |
| `extract_attribute` | On-demand attribute extraction (4.4) |
| `exclude_and_rerank` | When the user is not happy, exclude rejected products and rerank with updated conditions; call `fetch_more` if too few candidates remain |
| `fetch_more` | Fetch a few more products from a source |
| `calculate` | The LLM turns the user's rule into structured calculation steps; Python runs them and returns the result and the formula |
| `recheck_availability` | Recheck price and availability |
| `update_profile` | Edit the category profile |

### 4.9 Memory

- Conversation state is stored in LangGraph's Postgres checkpointer, so a conversation can be paused and resumed.
- The conversation holds product IDs only. Data is fetched with tools when needed. When a conversation gets long, older turns are summarised. Products and reviews live in the database and are not lost to summarisation.
- The next search in the same category reuses stored data. Prices and availability are always rechecked before the picks (4.7), so old data never goes straight into a recommendation.

### 4.10 Context budget

The database can keep growing. At each step, the model sees only what that step needs.

| Step | What the model sees | Limit |
|---|---|---|
| Extraction | One cleaned page. Very long pages are split by paragraph and extracted in parts | One page |
| Filtering and ranking | Nothing. SQL and Python do it | 0 |
| On-demand extraction | The chunks about this attribute for one product | Top 5 chunks per product |
| Picks | Short cards for the top products (core fields plus key specs, about 150 tokens each) plus each product's review summary | Top 10 products |
| Answering a question | The retrieved top k chunks | k = 10 by default |
| Follow-up | Only product IDs in the conversation; tools fetch data when needed | As needed |

### 4.11 LLM layer

The model provider is not decided and may be a free tier, so the LLM layer is not tied to one provider.

- One calling interface: send messages, request structured output, call tools. Gemini, Groq, OpenRouter and Ollama all offer OpenAI-compatible endpoints, so one client covers them by configuration. Claude is connected separately through the official Anthropic SDK.
- Each step has its own model setting. Extraction is simple and can use a free small model. Writing the picks and understanding follow-ups need a stronger one.
- Models are chosen by evaluation numbers (section 7), not by impression.
- The layer has its own rate limiter and retries, set per provider. Free tiers are slow: at 10 requests per minute, 50 pages that all need an LLM call take at least 5 minutes. JSON capture and JSON-LD keep most pages away from the LLM, and the share of pages that needed an LLM call is logged per search.

Candidates (quotas change often; check the official pages before signing up):

| Option | Strengths | Limits |
|---|---|---|
| Gemini free tier | Strongest free option, no card needed, good Chinese, structured output and tool calling | Flash models about 10 requests per minute and 1,000 to 1,500 per day; free-tier data may be used by Google to improve its products |
| Groq free tier | Very fast | About 6,000 tokens per minute, too small for long pages |
| OpenRouter free models | Many open models, useful for comparison | Rate-limited, the free list changes |
| Local Ollama | Free, data stays on the laptop | This laptop's RTX 3060 6 GB fits only 7B to 8B quantised models and shares memory with bge-m3 |
| Claude API | Most consistent quality | Paid |

## 5. Data model

One Postgres database with the pgvector extension.

| Table | Contents |
|---|---|
| `category_profiles` | Category profiles, versioned, marked as preset, generated or user-edited |
| `products` | The product itself: brand, model, category, `attributes` jsonb |
| `listings` | The product's page on one site: URL, rating, review count, fetch time. One product has many listings |
| `variants` | Size, color, capacity and so on, each with its own price and stock. One listing has many variants |
| `price_history` | Every fetched price and availability |
| `reviews` | Review records; `product_id` may be empty |
| `chunks` | Product text and review chunks: text, `vector(1024)`, BM25 tokens, language, embedding model version, source IDs |
| `source_stats` | Contribution of each source per category |
| `fetch_log` | URL, time, status and duration of each request, for rate limiting and debugging |
| `eliminations` | Products eliminated or rejected in a conversation, with the thread ID and why; not shown in the picks. `exclude_and_rerank` reads it so rejected products stay out for the rest of that conversation |
| LangGraph checkpoint tables | Conversation state |

## 6. Tech choices

- Python 3.10 in the conda environment `product-scout`, dependencies managed with uv
- LangGraph, with checkpoints in Postgres
- Provider-neutral LLM layer (4.11)
- bge-m3 embeddings, run locally; BM25 implemented by us on the bge-m3 tokenizer
- Postgres + pgvector (0.8 or later), run in Docker
- Playwright with a persistent browser profile
- PaddleOCR
- Langfuse for traces, cost and latency of every run
- A command-line interface in v1, then FastAPI and Docker deployment

## 7. Evaluation

Build a labelled set: about 50 product pages across the four categories and several sites. Record the true price and key specs by hand, and save page snapshots so tests can be rerun. For browser-based sites a snapshot includes the captured JSON responses as well as the HTML.

| Metric | How it is measured |
|---|---|
| Price extraction accuracy | Share of extracted prices that exactly match the label |
| Attribute extraction accuracy | Per attribute, including on-demand extraction |
| Calculation correctness | Python calculations covered by unit tests; about 20 examples test whether the LLM turns the user's words into the right calculation steps |
| Source faithfulness | Numeric claims checked automatically; text claims judged by an LLM, calibrated by manual spot checks |
| Availability accuracy | Products shown as available in the picks, spot-checked by hand |
| Product matching accuracy | About 50 hand-labelled review and product pairs |
| Retrieval quality | About 30 questions, comparing vectors only, BM25 only, hybrid, bge-m3 sparse output and long context on accuracy, cost and latency |
| Model comparison | The same extraction and recommendation tasks run on several candidate models, comparing accuracy, latency and cost, to choose a model per step |
| Search efficiency | How many fewer pages the Threshold Algorithm stopping rule fetches than reading every page |
| Run metrics | Cost and latency per search (Langfuse) |

"Did it pick the right product" has no ground truth and is not a metric.

## 8. Terms and data

- Check each source's terms before enabling it, and record the result in the adapter. Prefer official APIs.
- Fetching is for personal use only, at low volume and rate-limited. The user handles login and captchas.
- Fetched pages, reviews, snapshots and the browser profile stay out of the public Git repo (already in `.gitignore`). The public repo holds only code and evaluation numbers.
- Numbers in the CV must come from evaluations that actually ran, and Zheyun must confirm them.

## 9. Phases

The project is too big for one implementation plan, so each phase gets its own plan.

1. **Skeleton**: project structure, Postgres + pgvector, tables, the fridge preset profile, the LLM layer (one free model first), one product adapter, structured extraction, command-line picks, and the labelled set and evaluation for price extraction.
2. **Conversation**: LangGraph conversation loop, checkpoints, clarifying questions, follow-up tools, calculation tool, Langfuse.
3. **Text and retrieval**: chunking, bge-m3 embeddings, BM25, RRF, review adapters, product matching, on-demand extraction, pros and complaints in the picks.
4. **More sources and categories**: browser adapters for Taobao, JD and Amazon (with login checks and JSON capture), Xiaohongshu, Zhihu, the other three preset categories, profile generation for new categories.
5. **Search strategy**: predicate pushdown, Threshold Algorithm, multi-stage ranking, budget.
6. **Full evaluation**: all metrics, retrieval comparison, model comparison.
7. **Deployment**: FastAPI, Docker.

The first product adapter in phase 1 is chosen after the terms check. Phase 1 evaluates price extraction, so that adapter must have prices. Prisjakt (full prices, terms not yet checked) fits; EPREL (official API, accurate specs, no prices) can only be a second source for fridge specs, never the only one.

Review fetching (phase 2b and 2d in section 3) arrives in phase 3. Until then the Threshold Algorithm runs on rating and sales only.

## 10. Open items

- Terms check results for each site.
- Which model to connect first in phase 1. Suggestion: get a free Gemini key first.
- Store filter fields redundantly in `chunks`, or join with `reviews` and `products` at query time.
- Langfuse cloud or self-hosted in Docker.
- CLAUDE.md still describes the old incense-only scope and must be updated to match this document.
- The CV line drafted earlier mentions "the Claude API". Revise it once the model is chosen.
