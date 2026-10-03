# product-scout design

Date: 2026-10-02. Revised 2026-10-03 (v1 is amazon.se only). Language revised 2026-10-03 to about 80% ASD-STE100.
Status: awaiting review

Flowcharts: https://claude.ai/artifact/4jJqCkVudUFU24kTyXsiqZ

## 1. Goal

product-scout is a conversational agent. It helps the user pick products in any category. Two examples are a buyer who selects a fridge and a seller who selects incense to sell.

The project has two purposes. They are equally important.
- Practical use. Zheyun uses the agent for her own purchases and to select incense.
- CV. The project supports her applications for AI Engineer jobs. Thus it must be real, measured and deployable.

The system makes the list of candidates smaller. The user makes the final selection.

## 2. Scope

### In v1

- Four test categories: fridge, incense, headphones or small electronics, clothes or shoes.
- The agent clarifies the needs of the user, searches, and shows a small number of top picks. Then it answers all follow-up questions or suggests alternatives. The user can pause a conversation and continue it later.
- Source: amazon.se only, for products and for their shop reviews. Search terms are in Swedish. Prices are in SEK.
- The budget is optional. If the user gives a budget, it is a hard condition. Price is not a ranking factor. The agent sorts by price only when the user asks. Then it uses the sort function of the site.
- Text retrieval is hybrid. It uses vector search and BM25.
- Professional reviews, simple version (decided 2026-10-03). After the shop reviews in build phase 3, the agent searches one or two public professional review sites (for example Råd & Rön) by the model numbers of the candidates. The review text uses the same chunks and retrieval as the shop reviews. The picks show the source type of each claim.

### Not in v1

- Taobao. Taobao is the first source after v1. It adds login checks, JSON capture, Taobao shop reviews, the match of one product across Amazon and Taobao, and budgets in a second currency.
- Other product sources: JD and Swedish shops (Prisjakt, Elgiganten, Tradera, EPREL and others). The adapter interface is ready for them.
- Other review sources: community sites (Zhihu, Reddit, Flashback) and more professional review sites (Rtings, SweClockers and others). These sources also bring the category-level review search (phase 2b below).
- Xiaohongshu. The posts contain too many advertisements. We cannot trust them as reviews.
- Video reviews (YouTube, Bilibili). These come later. The difficult part is to get the transcripts. Cost is not the problem.
- Currency conversion, shipping, duty or moms by default. The system calculates these only when the user tells it how.
- A list of the removed products in the picks. The database keeps the removed products. The picks do not show them.
- Tags for the complaint types in reviews. We add tags later if we need counts.
- An LLM that controls a browser through MCP. The code opens pages in fixed steps. The LLM only reads the cleaned content.
- Copies or forks of code from other people. We read other projects only for ideas. An example is the method that MediaCrawler uses to crawl with a browser that is logged in.

## 3. Architecture

The agent uses LangGraph. Each product search is one conversation thread. Postgres keeps the state of the thread. There are four phases. Follow-up questions can go back to an earlier phase.

```
User: I want a fridge, my requirements are ...
   ↓
1. Clarify the needs
   Load the category profile. If there is no profile, the LLM writes one. The system saves it, and the user can edit it.
   The LLM compares the message of the user with the profile. If the needs are not clear, it asks questions until they are clear.
   ↓
2. Search and store
   Before the search starts, check the login for each site that this search needs and that requires a login.
   Ask the user to log in at the start.
   a. Search amazon.se in Swedish. Send the filters of the user to the site, and the budget if the user gives one.
      The result lists show the rating and the review count of each candidate.
   b. (After v1) Search external review sources at category level. Count how often each model is mentioned.
   c. Fetch the detail pages in Threshold Algorithm order over the sorted lists (see 4.3). Store them (see 4.4).
      Stop at 50 detail pages, or when no unread candidate can come into the top 10.
   d. Score the fetched candidates one time with the phase 3 scoring. For the top 10, fetch their shop reviews,
      up to 30 pages. Also search the selected professional review sites by the model numbers of the top 10.
   Check each fetched page for a login problem and for a captcha.
   ↓
3. Recommend
   Filter with SQL and score with Python. Take the top picks (default 5).
   Check the price and the availability of each pick again. Replace a sold-out pick with the next candidate.
   The LLM writes the picks and cites record IDs. Python checks the IDs and the numbers.
   ↓
4. Follow-up
   Question about a pick → retrieve and answer
   Calculation           → Python calculates and shows the formula
   User is not satisfied → remove the rejected picks, rank again, go back to phase 3
                           if too few candidates remain, go back to phase 2 and search more
```

## 4. Components

### 4.1 Category profile

Each category has one YAML file. The file tells the agent what to ask, which conditions are frequent, and where to search. A profile does not list all attributes of the category. The agent extracts the other attributes when it needs them (see 4.4).

```yaml
category: fridge
display_name: Fridge
attributes:            # frequent conditions and preferences
  - name: height
    label: Height
    type: number
    unit: cm
    kind: hard        # hard condition, removes products
  - name: noise
    label: Noise
    type: number
    unit: dB
    kind: soft        # preference, changes the rank
questions_for_user:
  - How wide, tall and deep is the space for the fridge?
  - What is your budget?
  - Is noise important to you?
complaint_types: [noise, frost, door seal, compressor life, after-sales service]
sources:
  products: [amazon_se]
  reviews: [amazon_se_reviews, professional_reviews]   # community sources and Taobao come after v1
```

- Attribute types: number with unit, enum, boolean, text.
- The agent does not use `complaint_types` to tag reviews. It uses the list to write the picks. When it summarizes the complaints for each product, it does one retrieval for each type.
- **Preset profiles.** v1 has the four test categories. Later there are about 10 frequent categories. After v1, external review sources come in. Then we find the review sources for each preset one time and write them into the profile.
- **New categories.** The LLM writes a draft profile. In v1 it uses amazon.se. After v1 it also searches for review sources for the category. The agent uses sources that have an adapter. A generic fetcher reads public pages. Sources that need a login or have strong anti-bot measures go on a list of "suggested new adapters".
- The user can edit a profile. The system saves and uses the edited version.
- Many products can show an attribute that the profile does not have. Then the agent asks the user if it must add the attribute to the profile.
- After each use, the agent records how many matched reviews each source gave. Sources that give few reviews move down the list for the next search.

### 4.2 Source adapters

Product sources and review sources use one interface. Each site has one adapter.

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

- `search` puts the hard conditions into the filters of the site when possible (predicate pushdown). It supports more than one sort order, for example rating and sales.
- The access method changes with the site. In v1 the agent fetches amazon.se in the browser of the user, at low volume. This is almost the same as when the user browses. Later sources with an official API use that API (EPREL, Tradera). The agent requests other public pages directly.
- **The browser is a dedicated Chrome profile.** It is not the usual browser window of the user. Playwright cannot open a profile that a running Chrome has locked. Also, recent Chrome versions do not allow automation on the default profile. The profile is in `browser-profile/`. The user logs in there one time, and the profile keeps the session.
- Product pages and search pages on amazon.se work without a login. The product page shows only a few reviews. Amazon shows the full review pages only after a login. Thus the shop reviews (build phase 3) need the user to log in to amazon.se in that profile.
- The agent searches each site in the language of that site. The LLM writes the search terms for each site from the needs of the user. For amazon.se the terms are in Swedish.
- **The adapter reads Amazon pages with hand-written selectors.** Amazon has no reliable JSON-LD. Thus the adapter uses CSS selectors to read the title, price, rating, review count, stock and variants on search result cards and detail pages. The agent calls the LLM only for fields that the selectors do not find. The agent logs each selector miss. Then we see a broken selector quickly.
- **After v1, browser sites such as Taobao read the JSON that the page requests.** On these sites, the page loads the product data through separate API calls. Playwright monitors the network responses and gets fields that are ready to use, without an LLM call. If this does not work, the adapter reads the rendered DOM.
- Each adapter records the check of the terms of service and its date. An adapter without a terms check stays disabled.

#### Login and captcha

- `check_session` finds if the user is logged in. Each of these conditions means "logged out": a redirect to the login page, a login dialog on the page, or captured JSON that says "not logged in" or "session expired". The agent handles a captcha in the same way.
- Before phase 2, the agent checks each site that this search needs and that requires a login. Thus it asks the user at the start. In v1 this is amazon.se, when the agent fetches shop reviews. The agent checks again on each page, because a session can expire or a captcha can appear during the search. The check for each page examines the page that the agent just fetched. It does not send an extra request. The availability checks in phase 3 use the same check.
- If there is a problem, the agent pauses with a LangGraph interrupt. It opens the login page of the site in the browser of the user and tells the user in the chat. An example message: "You are not logged in to amazon.se. I opened the login page. Reply 'done' after you log in, or skip the reviews." After the reply, the agent checks again and continues from the point where it stopped. If the user skips, the agent continues without the data that needs the login.
- The user always logs in and solves the captchas. The program never types passwords and never bypasses captchas. The sessions stay on the computer in `browser-profile/`. This folder is not in Git.

### 4.3 Search strategy

The search must not read an endless number of result pages. It must also keep products that are not related out of the results. The strategy uses these classic methods.

1. **Predicate pushdown.** Each hard condition that the site can filter goes into the search parameters of the site.
2. **Top-k over more than one sorted list (Fagin's Threshold Algorithm).** All lists must be available before the agent fetches a detail page. In v1 the lists are the sort orders of amazon.se, by customer rating and by popularity (or review count). The first task of build phase 5 finds which sorts amazon.se has. After v1, review mentions from the category-level review search (phase 2b) become a third list. The agent reads down the lists in parallel. It fetches the detail page of each new candidate. It stops when the best possible list score of every unread candidate is less than the score of the current k-th candidate (k = 10). The list score moves the rating near the category average when the review count is small (a Bayesian average). Thus a 5.0 rating from 2 reviews does not rank above 4.6 from 3,000 reviews. The final rank in phase 3 also uses data from the detail pages. Thus this stop rule only selects the detail pages that the agent fetches.
   On amazon.se each search card shows the rating and the review count. Thus a candidate's value in the other list costs nothing. Here the algorithm mainly decides how many result pages to read for each list. Its full benefit comes after v1, when the review-mentions list is expensive to read.
3. **Multi-stage ranking.** The first pass uses only the title, price, rating and review count from the search results. It removes products that are not related. If the user gives a budget, it also removes products above the budget. The agent fetches detail pages only for the candidates that pass. Only the top 10 after the rank get reviews and images.
4. **Page limits.** Each search has page limits. When the agent reaches a limit, it stops and says so in the picks. Defaults: 50 detail pages and 30 review pages. If we use a paid model, we add a money limit. We set it after phase 1 measures the cost of one search.

### 4.4 Extraction and storage

Principle: **the agent structures only the data that it uses to filter and compare. All other data stays as the original text, and retrieval finds it.** The agent removes scripts, menus and advertisements from each page. Then it divides the page into two paths.

**Structured path: a small number of fields**

1. Read the data in the order of reliability. First, captured JSON or structured data in the page (JSON-LD). Second, selectors for the site (amazon.se in v1). Third, the HTML text through the LLM.
2. Core fields: name, brand, model, price, currency, rating, review count, stock status, source URL and fetch time. Also extract the attributes in the profile and each attribute that the current conditions of the user use. The rank needs them. If a field is still missing after these steps, the LLM extracts it from the HTML, with one call for each page.
3. Validate with Pydantic.
4. Use the model number, the brand and the specifications to find if the product is already in the database. If it is, add or update the listing and the variant. If it is not, make a new product. Each fetched price also adds a row to price_history.

Rule: **prices come only from structured data or HTML, never from images.** Prices in product images are often old.

**Text path: keep all text**

1. The agent divides product text, descriptions and review text into chunks. A short review is one chunk. The agent divides long text at paragraphs into chunks of about 300 to 500 tokens, with a small overlap. Each chunk starts with the title and the product name.
2. The agent reads images only when key data is missing from the text and is only in the images. PaddleOCR-VL reads the images. It is a 0.9B vision-language model from the PaddleOCR team, and it runs on the local computer. In one step, it changes the text, tables and charts in an image into markdown. This markdown goes into the text path, the same as other text. Thus later questions and BM25 find the image text, and the agent does not read the image again. If PaddleOCR-VL gives no usable result (for example on advertisement images with a heavy design), the worker model reads the image (4.11). The agent cuts long images into segments first. We did not select plain OCR such as PP-OCRv6. It gives lines of text and loses the table structure. An LLM must then make the tables again.
3. Each chunk gets an embedding and BM25 tokens (see 4.6). The agent stores it in `chunks`.

**On-demand attribute extraction**

Sometimes the user adds a condition on an attribute that the database does not have, for example "not taller than 180 cm". Then the agent retrieves the chunks about that attribute for each candidate. The LLM extracts the value. The agent writes the value to `attributes` and records the ID of the source chunk. If the agent finds no value, it marks the value as missing and says so in the picks.

The model must not guess. Thus each extracted value must include the raw text that it comes from (`raw`). Python checks that this text is in the source page or chunk. If the check fails, the agent removes the value and marks it as missing.

The format of each value in `attributes`:

```json
{"height": {"value": 178, "unit": "cm", "raw": "1780mm", "source_chunk_id": 1234}}
```

Attributes that are not in the profile go in `attributes.extra`.

### 4.5 Reviews and product matching

Review fields: source URL, platform, author type (buyer, blogger, professional reviewer), publish date, matched product or "unmatched", original text, language, fetch time.

In v1, shop reviews are already attached to a listing, so no matching is necessary. Professional reviews in v1 are found by model number, so they match on the model number (step 1 below). If a test covers a different model of the same series, the agent files it under the brand and does not attach it to the product. After v1, external sources bring community reviews. These reviews often do not give the model number. The agent must match them to a specific product.
1. Exact match on the model number, if there is one
2. If not, a fuzzy match on brand, category and key specifications
3. Last, the LLM decides if it is the same product

The agent does not force a review onto a product if it cannot match it. It files the review under the brand or the category as general reference.

### 4.6 Text retrieval

Product text chunks and review chunks use one retrieval path.

- **Vector search.** bge-m3 runs on the local computer and makes 1024 dimensions for each chunk. pgvector stores the vectors with an HNSW index. Chinese, Swedish and English use one vector space.
- **BM25.** We write it in Python. The bge-m3 tokenizer makes the tokens, so all three languages use one code path. A regex also adds each model number (for example `RB38C7B5D22`) as one token, because subword tokenization breaks model numbers into parts.
- **Merge.** Reciprocal Rank Fusion (RRF) merges the two result lists. The score of each result is the sum of `1/(60 + rank)` over the two lists.
- **Filters.** Conditions such as product scope, author type and publish date apply to the two searches. With strict filters, the HNSW index of pgvector can give too few results. Thus we use the iterative scan that pgvector 0.8 added.
- Each chunk records the version of the embedding model that made it. If the model changes, we know which chunks need a new embedding.
- The same retrieval interface also has a long-context implementation. We use it only as the baseline for evaluation.

### 4.7 Picks

1. The needs of the user and the category profile that the agent used
2. Comparison table: price, key specifications and rating of the top picks (default 5). Each cell has a link to its source.
3. Pros and complaints for each product, with the source type (professional test, buyer review, community user)
4. Missing data and data that is not certain
5. Fetch time and availability
6. How the agent made the rank

**Which price to show.** If a product has variants, the picks show the price of the variant that agrees with the needs of the user (size, color, capacity). If this variant is not known, the picks show the price range of the variants. The budget check uses the lowest price of the variants that agree.

**Price and currency.** The agent uses the price only for the budget, and only if the user gives one. The price does not change the score. In v1 all prices are in SEK. After v1, with Taobao, the agent checks the budget for each site in the currency of that site. The picks from the two sites are side by side, and each price is in its own currency. The system never converts currencies on its own. If the user asks to sort by price, the agent uses the sort of the site within one site, or asks the user how to compare across sites.

**Before the agent shows the picks, it checks the price and the availability of each pick again. It does this also when the fetch was recent.** If a pick is sold out, the next candidate replaces it, and the agent checks that candidate too. The agent does not check again the candidates outside the top.

The agent makes the picks as structured output. Each claim includes the record IDs that it cites. Python checks that the IDs exist and that the numbers agree with the records. If they do not agree, the LLM writes the picks again.

### 4.8 Follow-up tools

| Tool | Purpose |
|---|---|
| `query_products` | Query the product tables with structured conditions. The LLM never writes SQL |
| `search_text` | Text retrieval (4.6), to answer questions about a product |
| `extract_attribute` | On-demand attribute extraction (4.4) |
| `exclude_and_rerank` | When the user is not satisfied, remove the rejected products and rank again with the new conditions. Call `fetch_more` if too few candidates remain |
| `fetch_more` | Fetch a small number of more products from a source |
| `calculate` | The LLM changes the rule of the user into structured calculation steps. Python does the steps and gives the result and the formula |
| `recheck_availability` | Check the price and the availability again |
| `update_profile` | Edit the category profile |

### 4.9 Memory

- The LangGraph Postgres checkpointer keeps the conversation state. Thus the user can pause a conversation and continue it later.
- The conversation keeps only product IDs. Tools get the data when the agent needs it. When a conversation becomes long, the agent summarizes the older turns. Products and reviews stay in the database. The summary does not remove them.
- The next search in the same category uses the stored data again. The agent always checks prices and availability again before the picks (4.7). Thus old data never goes directly into a recommendation.

### 4.10 Context budget

The database can become very large. At each step, the model sees only the data that the step needs.

| Step | What the model sees | Limit |
|---|---|---|
| Extraction | One cleaned page. The agent divides very long pages at paragraphs and extracts each part | One page |
| Filter and rank | Nothing. SQL and Python do this step | 0 |
| On-demand extraction | The chunks about this attribute for one product | Top 5 chunks for each product |
| Picks | Short cards for the top products (core fields and key specifications, about 150 tokens each) and the review summary of each product | Top 10 products |
| Answer to a question | The top k retrieved chunks | k = 10 by default |
| Follow-up | Only the product IDs in the conversation. Tools get data when necessary | As necessary |

### 4.11 LLM layer

We have not selected the model provider, and it can be a free tier. Thus the LLM layer is not tied to one provider.

- One interface sends messages, requests structured output and calls tools. Gemini, Groq, OpenRouter and Ollama all have OpenAI-compatible endpoints. Thus one client, with a configuration, works for all of them. Claude connects separately through the official Anthropic SDK.
- **Two model roles.** By default, each step uses one of two roles. For evaluation, we can point each step at a different model.

| Role | Steps | Calls for each search |
|---|---|---|
| `chat`, a strong model | Write a draft category profile. Compare the message of the user with the profile and ask questions. Write the search terms for each site. Write the picks. Read follow-up questions, select tools, answer questions, change calculation rules into steps | About 10 to 30 |
| `worker`, a light multimodal model | Extract the fields that the selectors do not find. On-demand attribute extraction. Read images that PaddleOCR-VL cannot read. After v1, decide if two listings are the same product | Tens to about 200 |

- The search terms use the strong model. There are few of them, and bad terms make all later candidates bad. The worker must accept images, so we do not need a third model. bge-m3 (embeddings) and PaddleOCR-VL (images) run on the local computer and are not part of this layer.
- The judge for source faithfulness (section 7) must not be the model that it judges. Thus the full evaluation can add a third model only for this judgement.
- First setup: one free Gemini key for the two roles. A Flash model is `chat` and a Flash-Lite model is `worker`. The worker makes most of the calls, and Flash-Lite has the larger daily quota. We check the exact models and quotas on the official pages during the setup.
- We select models by evaluation numbers (section 7), not by impression.
- The layer has its own rate limiter and retries for each provider. Free tiers are slow. At 10 requests each minute, 50 pages that all need an LLM call take at least 5 minutes. Selectors, JSON-LD and (after v1) JSON capture keep most pages away from the LLM. The agent logs the percentage of pages that needed an LLM call for each search.

Candidates (quotas change frequently. Check the official pages before you sign up.)

| Option | Strengths | Limits |
|---|---|---|
| Gemini free tier | The strongest free option. No card is necessary. Good Chinese, structured output and tool calls | Flash models allow about 10 requests each minute and 1,000 to 1,500 each day. Google can use free-tier data to improve its products |
| Groq free tier | Very fast | About 6,000 tokens each minute, too small for long pages |
| OpenRouter free models | Many open models, useful for comparison | Rate limits. The free list changes |
| Local Ollama | Free. The data stays on the laptop | The RTX 3060 6 GB in this laptop holds only quantized 7B to 8B models, and it shares memory with bge-m3 and PaddleOCR-VL. We use it only for comparison in the evaluation |
| Claude API | The most consistent quality | Paid |

## 5. Data model

One Postgres database with the pgvector extension.

| Table | Contents |
|---|---|
| `category_profiles` | Category profiles with versions, marked as preset, generated or edited by the user |
| `products` | The product: brand, model, category, `attributes` jsonb |
| `listings` | The page of the product on one site: URL, rating, review count, fetch time. One product has many listings |
| `variants` | Size, color, capacity and other options, each with its own price and stock. One listing has many variants |
| `price_history` | Each fetched price and availability |
| `reviews` | Review records. `product_id` can be empty |
| `chunks` | Product text and review chunks: text, `vector(1024)`, BM25 tokens, language, embedding model version, source IDs |
| `source_stats` | The contribution of each source for each category |
| `fetch_log` | URL, time, status and duration of each request, for rate limits and debugging |
| `eliminations` | Products that the agent removed or the user rejected in a conversation, with the thread ID and the reason. The picks do not show them. `exclude_and_rerank` reads this table, so rejected products stay out for the remaining conversation |
| LangGraph checkpoint tables | Conversation state |

## 6. Tech choices

- Python 3.10 in the conda environment `product-scout`. uv manages the dependencies
- LangGraph, with checkpoints in Postgres
- An LLM layer that is not tied to one provider (4.11)
- bge-m3 embeddings on the local computer. Our own BM25 on the bge-m3 tokenizer
- Postgres + pgvector (0.8 or later) in Docker
- Playwright with a dedicated persistent Chrome profile
- PaddleOCR-VL for images, on the local computer
- Langfuse for traces, cost and latency of each run
- A command-line interface in v1. Then FastAPI and Docker deployment

## 7. Evaluation

Make a labelled set of about 50 amazon.se product pages across the four categories. Record the true price and the key specifications by hand. Save page snapshots, so we can do the tests again. After v1, Taobao snapshots also include the captured JSON responses.

| Metric | How we measure it |
|---|---|
| Price extraction accuracy | The percentage of extracted prices that are the same as the label |
| Attribute extraction accuracy | For each attribute, with on-demand extraction included |
| Calculation correctness | Unit tests cover the Python calculations. About 20 examples test if the LLM changes the words of the user into the correct calculation steps |
| Source faithfulness | An automatic check for numeric claims. An LLM judges text claims, and manual spot checks calibrate the judge |
| Availability accuracy | Spot checks by hand of the products that the picks show as available |
| Product matching accuracy | After v1, with Taobao and external review sources: about 50 pairs labelled by hand. We do not measure it in v1, because v1 has no matching |
| Retrieval quality | About 30 questions. Compare vectors only, BM25 only, hybrid, bge-m3 sparse output and long context for accuracy, cost and latency |
| Model comparison | Do the same extraction and recommendation tasks on several candidate models. Compare accuracy, latency and cost, and select a model for each role or step |
| Image reading | About 15 product images with specifications. PaddleOCR-VL and the worker model read them. Compare the results with labels made by hand |
| Search efficiency | How many fewer pages the Threshold Algorithm stop rule fetches, compared with all pages |
| Run metrics | Cost and latency of each search (Langfuse) |

"Did it pick the right product" has no ground truth and is not a metric.

## 8. Terms and data

- Check the terms of each source before you enable it. Record the result in the adapter. Use official APIs when possible.
- Fetches are for personal use only, at low volume and with rate limits. The user does the logins and the captchas.
- The terms of Amazon restrict automated access. On 2026-10-03 Zheyun decided that personal, low-volume fetches of amazon.se in her own browser profile are acceptable to her. The Amazon adapter records this decision and its date. The terms of Taobao also restrict it. She decides before we build the Taobao adapter after v1.
- The free Gemini tier can use requests to improve the products of Google. The requests contain public product pages and the shopping needs of the user.
- Fetched pages, reviews, snapshots and the browser profile stay out of the public Git repository (they are already in `.gitignore`). The public repository contains only code and evaluation numbers.
- Numbers in the CV must come from evaluations that we really did, and Zheyun must confirm them.

## 9. Phases

The project is too large for one implementation plan. Thus each phase has its own plan.

1. **Skeleton.** Project structure, Postgres + pgvector, tables, the fridge preset profile, the LLM layer with the `chat` and `worker` roles on a free Gemini key, the dedicated browser profile, the amazon.se adapter with selectors and the captcha check, structured extraction, command-line picks, and the labelled set and evaluation for price extraction on amazon.se.
2. **Conversation.** The LangGraph conversation loop, checkpoints, clarifying questions, follow-up tools, the calculation tool, Langfuse.
3. **Text and retrieval.** Chunks, bge-m3 embeddings, BM25, RRF, amazon.se shop reviews with the login check, on-demand extraction, pros and complaints in the picks. At the end, professional reviews by model number from one or two public review sites (simple version).
4. **Images and more categories.** Image reading with PaddleOCR-VL and the worker as the fallback, the other three preset categories, profile generation for new categories.
5. **Search strategy.** Predicate pushdown, Threshold Algorithm, multi-stage ranking, page limits.
6. **Full evaluation.** All metrics, the retrieval comparison, the model comparison.
7. **Deployment.** FastAPI, Docker.

Shop reviews (phase 2d in section 3) come in build phase 3. Taobao, external sources, the category-level review search (phase 2b) and other shops come after v1. Taobao is first.

## 10. Open items

- The exact Gemini models and quotas. We check them when we make the key.
- Which sort orders amazon.se has (build phase 5).
- Which professional review sites to use, and their terms checks. Decide before build phase 3.
- Keep the filter fields twice in `chunks`, or join with `reviews` and `products` during the query.
- Langfuse cloud, or Langfuse in Docker on our computer.
- CLAUDE.md still describes the old scope (incense only). We must update it to agree with this document.
- The CV line from before mentions "the Claude API". Change it when we select the model.
