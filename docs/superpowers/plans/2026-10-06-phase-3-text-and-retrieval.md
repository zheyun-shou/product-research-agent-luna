# Phase 3 Text and Retrieval

> **How to use this plan.** Zheyun answered the questions on 2026-10-06 (see her decisions). Commit at the end of each task. Do not run a paid evaluation until she asks.

**Goal:** The agent finds the right text of many products and many reviews, not only the full text of one product. It uses hybrid retrieval (bge-m3 vectors, BM25 and RRF). It reads the shop reviews behind the login, finds attribute values that the database does not have, and writes pros and complaints in the picks. An evaluation compares the retrieval methods with the long-context baseline.

**Why:** On 2026-10-06 the database has 24 listings, 23 page texts and 114 reviews. The full text of one product fits in the prompt, so `read_product` works now. But the model cannot ask a question across all products ("which ones have reviews about noise") without reading each of them. With the shop reviews (about 10 reviews on each review page), the text becomes too large for the prompt.

**Spec:** sections 4.4 (text path, on-demand extraction), 4.5, 4.6, 4.7 (pros and complaints), 4.8 (`search_text`, `extract_attribute`), 7 (retrieval quality) and phase 3 in section 9.

**What exists now:**

- Postgres 17 with pgvector 0.8.7 in Docker. The extension is on, but no table has vectors.
- `page_texts` keeps specs, bullets, description, the reviews on the product page and the image links of each listing.
- `read_product` puts the full text of a pick into the prompt (at most 12000 characters). This is the long-context implementation of the spec.
- The fridge profile has `complaint_types`: noise, frost, door seal, compressor life, after-sales service.
- The GPU is an RTX 3060 Laptop with 6 GB. No torch and no embedding library are installed.

## Decisions of Zheyun (2026-10-06)

1. **Local model and libraries: yes.** torch with CUDA, FlagEmbedding and bge-m3 (from Hugging Face) go into the conda env and the Hugging Face cache. FlagEmbedding gives the dense vector and the sparse weights of bge-m3 in one call.
2. **Shop reviews: read the good and the bad ones.** The agent reads the positive and the critical review pages of each top pick (amazon.se has a star filter for each). When amazon.se is not logged in, the agent tells the shopper to log in in the dedicated browser window, and waits. Limits: the top 10 picks, at most 3 pages for each pick, at most 30 pages for each search.
3. **Claude makes the evaluation set.** Claude writes about 30 questions from the stored text, marks the chunks that hold each answer and copies the exact words. Python checks that the words are in those chunks. The DeepSeek grader marks the answers of the chat model. The plan and any CV text say "made by Claude, checked by Python", not "labelled by hand".
4. **Professional reviews: a free source for all categories.** She asked for a free source that covers all kinds of products, for example YouTube or a web search. See Task 9.
5. **A web page for the chat.** At the end, the shopper talks to the agent on a web page. This comes into the schedule, so that Zheyun can test the agent herself.

## Global constraints

- The user logs in and solves captchas herself. The program never types passwords and never bypasses captchas.
- Review pages, snapshots and the model files stay out of Git.
- Reviewer names are never stored.
- bge-m3 runs on the local computer. It costs no API money.
- Tests never load the real model. They use a fake embedder that gives fixed vectors.
- Each chunk records the embedding model and its version, so we know which chunks need a new vector.

## Tasks

### Task 1: Chunks (done)

On 2026-10-06 `scout backfill-text` made 181 chunks from the stored pages: 41 specs, 26 description and 114 review chunks. A chunk has 423 characters on average for a review and about 1,200 to 1,600 for specs and descriptions. The chunks are made each time a newer product page is stored. Until Task 2, the size is counted as 4 characters for each token.


- New table `chunks`: id, listing_id, kind (spec, bullets, description, review, image_text), ref (for example the review number), text, tokens (BM25 tokens, `text[]`), embedding `vector(1024)`, sparse (`jsonb`, token weights), embed_model, created_at.
- HNSW index on the embedding (cosine). Queries with strict filters use `hnsw.iterative_scan = relaxed_order` (pgvector 0.8).
- `chunk_page(record)`: one short review is one chunk. Long text is divided at paragraphs into chunks of 300 to 500 tokens with a small overlap. Specs and bullets of one product make one chunk if they are short. Each chunk starts with the product title.
- Reviews keep title, rating, date and verified in the chunk metadata, never the author.
- Tests: the division, the overlap, the title prefix, no author.

### Task 2: Embeddings with bge-m3 (done)

On 2026-10-06 Claude installed torch 2.6.0 (CUDA 12.4) and FlagEmbedding 1.4.2. The model download is about 4.3 GB, because the model repository also has an ONNX copy. `scout embed` gave the 181 chunks their vectors in 17.5 s on the RTX 3060. Chunk sizes are still counted as 4 characters for each token.


- `Embedder` protocol: `embed(texts) -> (dense, sparse)`. `BgeM3Embedder` uses FlagEmbedding on the GPU in fp16, with batches. `FakeEmbedder` for tests.
- `scout embed`: makes the chunks of each `page_texts` row that has none, and the vectors of each chunk without a vector or with an older `embed_model`. It prints the counts and the time.
- New fetches make chunks and vectors when the record is saved, if the embedder is available. If not, `scout embed` does it later.

### Task 3: BM25 and the retrieval interface (done)

A first try on 2026-10-06, with four queries, showed that each method gives results. BM25 gives nothing for a Chinese query, because no stored text is in Chinese. The vector search finds reviews about noise for it. Each chunk starts with the product title, so BM25 gives all chunks of a product the same score for its model number. A search takes 0.05 s (BM25) to 0.3 s (hybrid).


- BM25 in our own Python code (k1 = 1.2, b = 0.75). The tokens come from the bge-m3 tokenizer. A regex adds each model number (for example `RB38C7B5D22`) as one token.
- `Retriever` protocol: `search(query, scope, k=10) -> list[Hit]`. scope sets the listings, the chunk kinds and the dates.
- Implementations: `VectorRetriever`, `Bm25Retriever`, `SparseRetriever` (bge-m3 sparse weights), `HybridRetriever` (RRF, `1/(60 + rank)`), `LongContext` (all text of the scope, the baseline).
- Tests with a small fixed corpus and the fake embedder: each method finds the expected chunk, RRF order, model number tokens, filters.

### Task 4: `search_text` tool (done)

The bge-m3 model loads at the first `search_text` call in `scout chat`, and new chunks get their vectors before each search. No real chat turn has used the tool yet.


- New lookup tool `search_text(question, picks=None, kinds=None)`. It gives the top 10 chunks, each with its chunk id, its pick or product and its kind. With no picks it searches all stored products of the category.
- `read_product` stays. The model chooses: `search_text` for a question across many products or many reviews, `read_product` for one product.
- Numbers in the chunks can be used in the reply, as with the other lookups.

### Task 5: Shop reviews behind the login (done)

On 2026-10-06 Zheyun logged in, and Claude saved real review pages of the Bomann KG 320 outside Git. Findings:

- The review pages show the reviews of all Amazon shops of the EU. "From Sweden" had 0 reviews for this fridge. The other reviews are in German, Spanish, French, Dutch and Italian. bge-m3 is multilingual, so this is not a problem for the search.
- amazon.se ignores `pageNumber` in the URL. Page 2 gave the same 10 reviews as page 1. The "Show 10 more reviews" button loads more with a script. So the third page of a pick is the critical reviews sorted by `recent`. On the Bomann it gave 4 new reviews of 10.
- Each review has a stable id (for example `R2GOTWYJOAXP42`). The parser reads only the title, the rating, the date, the verified badge, the variant and the original text. It never reads the reviewer block or the hidden translation.

What was built:

- New table `shop_reviews`: one row for each review, with the filter that found it. Its chunks have the source `review_page` and the ref `shop review <id>`. A review that the product page shows too gets no second chunk.
- `scout.reviews.read_reviews`: for each pick, the top positive page, the top critical page, then the newest critical reviews when the filter has more than 10. Limits: 10 picks, 3 pages for each pick, 30 pages for each search. The table `review_reads` keeps the last read of each listing, also when it found no review. A complete read is not repeated for 7 days. When the shopper skips a login or a captcha, the step stops, and a read that the skip or the page limit cut short is repeated by the next search. The step runs only after a turn that showed new picks.
- The graph has a new step `reviews` after `recommend`. It reads the reviews of the picks that are shown (default 5), not of the top 10 candidates, because the state keeps only the picks. The web page now sends the replies of each step when the step ends, so the picks show before the reviews are read.
- `scout reviews ASIN...` and `scout parse-reviews PATH`. `scout chat` also prints the replies of each step when the step ends.

First real run (2026-10-06, 3 fridges): 9 review pages, 75 reviews (30 positive, 45 critical). 22 of them were on the product pages too, so they got no second chunk. The other 53 gave 54 chunks (one long review has two). `scout embed` gave 67 chunks vectors in 7.3 s: these 54 and 13 product page chunks from the web test of Zheyun. A Chinese question about noise ("冰箱噪音大吗") found two German reviews about a loud fridge in its top 2.

Plan as written before the build:

- Parser for the review pages of amazon.se: title, rating, date, verified purchase, variant, text. No author.
- Login check on each review page, from the page that was just fetched (no extra request). When the login is gone or a captcha shows, the agent tells the shopper to log in or solve it in the dedicated browser window, and waits. It never types a password.
- After the picks, the agent reads the positive and the critical review pages of the top 10, within the page limits. The reviews go into `page_texts` and into chunks. Each review keeps which filter found it.
- Step that needs Zheyun: she logs in, and Claude saves one review page as a test fixture outside Git. The parser is written against that page.

### Task 6: On-demand attribute extraction (done)

Built on 2026-10-06. On that day the stored fridges had few values for the conditions: height, width and depth on 7 of 22, total volume on 4, freezer volume on 2, noise on 3.

- `scout.extract.ondemand.fill_missing` runs in the picks after the title screening. It takes the first 15 candidates that miss a value of a condition. For each candidate it does one retrieval for all its missing attributes (5 chunks for each, at most 10), from the specs, description and image text of all listings of the product (not the reviews). One worker call reads all the missing values of the candidate, each with `raw` and `chunk_id`. The 15 places go only to candidates that still need a search.
- Python keeps a value only when the chunk id was given, the raw text is in that chunk, and the raw text shows the value with a unit word (the same check as the page extraction). The value goes into `products.attributes` with `source_chunk_id`, and also with the listing and the ref of the chunk, because chunk ids change when a newer page is stored. Then the size plausibility check, the conditions and the rank run again, so a found value can remove a candidate.
- The table `attribute_searches` records each search with an md5 hash of the text that it read, also when it found nothing. The same text is not searched again. A recheck of the same page and new review chunks do not change the hash. When the worker is not available, the values found before stay, and the note says that the search stopped early. An error of the text search does not stop the picks.
- `scout eval-chat` does not use it (`find_missing=False`), so its runs stay comparable with the earlier runs.
- Real run (2026-10-06, condition height up to 180 cm, Gemma 4 26B as the worker): 6 products searched, 3 values found, all raw texts in the cited chunks. Two were right (180 cm and 54 cm). The third was 1 cm, from "10D x 10B x 10H millimeter" on a 173 l Bomann. The page really says it, so the raw-text check passed.
- Claude first added plausible ranges to the profile for such values. Zheyun decided against it: the chat model can see this itself, so no Python rule. The ranges were removed. Test (2026-10-06): GPT-6 Luna wrote the picks for this fridge (height 1 cm) and the Bomann French Door (180 cm), with the usual prompt and no hint. For the 1 cm fridge it wrote "the unusually low height value may be a data issue". It did not doubt the other fridge. The Python conditions still count 1 cm as up to 180 cm, so the pick stays, with the remark of the model. One call, 0.0002 USD. The older Python rule of phase 1 (ignore height, width and depth when the box cannot hold the stated volume) was removed too, on her decision. The worker size check on each pick (phase 2 Task 11) stays, because it is a model judgement.

Plan as written before the build:

- When the shopper sets a condition on an attribute that a candidate does not have (for example "not taller than 180 cm"), the agent retrieves the chunks about that attribute for that candidate.
- The worker extracts the value with `raw` and `source_chunk_id`. Python checks that `raw` is in that chunk. If not, the value is missing and the picks say so.
- The value goes into `attributes` in the spec format.

### Task 7: Pros and complaints in the picks (done)

Built on 2026-10-06.

- The step runs after the shop reviews of new picks are read, so the new reviews count. For each pick: one retrieval for each complaint type of the profile and one for the praise, 3 review chunks each, over all listings of the product.
- One chat call for each pick writes one to three pros and complaints. They are qualitative ("most buyers", "a few"), never counts. Each cites the ids of its review chunks. Python checks that each statement cites at least one review, that the ids are reviews of this product, and that each number in the text is in a cited review. Up to 3 rounds with the problems.
- The review pages over-represent critical reviews (one positive page, two critical pages). The prompt gives the overall rating and the number of ratings, and tells the model to judge with them in mind.
- A pick with fewer than 3 review chunks gets "Too few reviews to summarize". The checked statements and their sources are kept with the picks in the state. An LLM outage keeps the picks and says that no summary was made.
- The summaries are kept in the state by listing. A pick that stays from an earlier turn keeps its summary, so a removal does not write all summaries again. When the model is not available, the summaries written before stay. The text says if a summary failed the checks or if the model gave no usable reply. No message is sent when no pick has enough reviews.
- `scout eval-chat` does not use it (`write_opinions=False`), because its scripts measure the conversation. The summaries need their own measurement: how many pass the checks, and a judge or a hand check of whether the statements agree with the cited reviews. This belongs with Task 8 or the full evaluation.
- Real run (2026-10-06, the 3 fridges with shop reviews, GPT-6 Luna, summaries in Chinese): all 3 passed the checks in the first round. Each pick got two pros and three complaints, qualitative, with mixed views stated (for example on noise). Each statement cited 1 to 6 reviews. A spot check by Claude: "dented on delivery" cites review 226, which says "Beschädigt geliefert". 3 calls, 0.0017 USD, 51 s.

Plan as written before the build:


- For each pick, one retrieval for each complaint type of the profile, over its review chunks.
- The chat model writes one or two pros and complaints for each pick. Each one cites chunk ids. Python checks that the ids exist and belong to that pick.
- The statements are qualitative, without counts (decided 2026-10-06), for example "a small part of the buyers say that it is noisy, most say that it is quiet". The picks show the source type of each statement (buyer review or test).

### Task 8: Retrieval evaluation

- `eval/retrieval/questions.yaml`: about 30 questions in Swedish, English and Chinese. Some are about one product, some are across products, some name a model number. Each has the gold chunk ids and the exact words.
- `scout eval-retrieval`: for each method, recall@10, MRR, the tokens put into the prompt and the time. No API cost.
- With Zheyun's permission: the chat model answers each question with the top 10 chunks of each method and with long context. The DeepSeek grader marks each answer. The report gives accuracy, tokens, cost and latency for each method.
- The results go into this plan, with the size of the corpus. If the corpus is too small to show a difference, the plan says so.

### Task 9: Web search (decided 2026-10-06)

Zheyun decided the positioning on 2026-10-06 (spec section 1a). Web search finds models, tests, forum views and product pages of other shops. amazon.se stays the deep source. Her terms decision covers the pages that web search finds.

- Tavily through its official API (free plan, 1,000 searches each month, no card). Zheyun makes the account and puts `TAVILY_API_KEY` in `.env`.
- Lookup tool `search_web(question)`. The searches use the model numbers of the picks and the needs of the shopper.
- The agent reads the found pages in parallel with several worker calls. Pages of one shop are still one at a time.
- Test articles and forum pages become chunks with the source `web`, the URL and the type (test or forum). Product pages of shops go through a generic reader: the schema.org `Product` JSON-LD first, then the worker with the raw-text check. They become listings, matched by GTIN when the page has one.
- The reviews on these product pages are buyer reviews. Reviews with the same text are kept one time.
- Tests: a fake Tavily client and saved pages; JSON-LD fixtures for the generic reader.

**Part 1 built on 2026-10-06: `search_web`.** `read_page` and the generic reader of shop pages are not built yet.

- `scout.websearch.TavilyClient` calls the REST API with a basic search (1 credit), 5 results and `include_raw_content`, so Tavily gives the text of each page and product-scout fetches nothing itself. A status 401, 429, 432 or 433 or no answer raises `WebSearchUnavailable`. Then the tool tells Luna, and the web floor is off for the rest of the turn.
- The tool `search_web(question, ids)`. Without ids, it gives the titles, the addresses and the short parts of the found pages, with no model call. Luna uses this to find which models testers recommend. With ids (at most 5), the worker reads each page in parallel (4 threads). It says the kind of page (test, forum, shop, other) and copies the sentences about each product. Python keeps a quote only when it is in the page text. The quotes of tests and forums become chunks with the source `web`, the kind `test` or `forum`, and the URL, the site and the title in `meta`. Thus `search_text` finds them later. Shop pages are only named in the report.
- The table `web_searches` keeps each question, the listings it was for, and the results with their text (cut at 30,000 characters for each page). The same question in the next 7 days uses the stored results and costs no credit.
- The floor of the design (section 5): before `show_picks`, each pick must have a web search with its id in the last 7 days, by any listing of its product. The shopper can switch it off (`update_needs` with `web_tests` false). Without a Tavily key there is no tool and no floor.
- A claim with the field `web` passes when the stored web quotes of the product show its numbers.
- Limits for each turn: 20 web pages and 10 web searches. The hidden-target test counts both.
- First real search through the client (2026-10-06, "Gorenje RK4182PS4 test recension", no products, so no model call): 5 results in 2.4 s, all with page text (117 to 25,772 characters). They were three YouTube pages, amazon.de and coolblue.de. No Swedish test site came. So the words of the question matter, and Luna chooses them.
- Not done yet: `summarize_reviews` reads only the shop reviews, not the web quotes, and the picks do not show the source type of each statement yet.

Findings from before the decision (2026-10-06): a Google search by the program is not an option (no automated searches, captchas). The YouTube Data API does not give transcripts. Brave Search needs a card.

## Additions of 2026-10-06

Zheyun read the comparison with two open-source agents and said to do all the proposals. The two agents are Microsoft InteRecAgent (a conversational recommender: query, retrieval and ranking tools, a plan made first, a critic, a user profile, a simulated user for evaluation) and LangChain open_deep_research (a supervisor with the tools think, conduct research and research complete; researchers that call tools by their own choice; limits on loops; a cheap model for summaries). We read their papers and documents only. We do not copy their code.

### Worker model (done)

On 2026-10-06 Claude tried 7 cheap OpenRouter models that read images and call tools. Task 1: keep the titles of the 24 stored listings that are a fridge with a freezer part (Claude labelled 23 titles from the titles; one is unclear and not scored). Task 2: the colour and the number of doors of the FOHERE BC-90 from its 6 product images (black, one door).

| Model | Titles right | Image right | Time (titles, image) | Cost of both |
|---|---|---|---|---|
| google/gemma-4-26b-a4b-it | 23/23 | yes | 1.3 s, 5.4 s | 0.0002 USD |
| google/gemini-3.5-flash-lite | 23/23 | yes | 1.0 s, 2.3 s | 0.0027 USD |
| openai/gpt-6-luna | 23/23 | yes | 5.8 s, 3.4 s | 0.0018 USD |
| qwen/qwen3.7-flash | 23/23 | yes | 25.3 s, 12.6 s | 0.0007 USD |
| xiaomi/mimo-v2.6-flash | 23/23 | yes | 31.3 s, 7.6 s | 0.0018 USD |
| z-ai/glm-5.3-flash | 22/23 | not run with this question | 8.0 s | 0.0031 USD |
| inclusionai/ling-3.0-flash-vl | 23/23 | error 400 on images | 4.2 s | |

The worker is now Gemma 4 26B on OpenRouter, with the free Gemini key as the fallback. This test is small and the tasks are easy. The extraction tasks (Task 6) show more.

### Task 10: think tool

A tool `think(note)` that does nothing. It lets the chat model write its reasoning between lookups. It counts as a lookup in the limit. The note is in the trace, never shown to the shopper. A "research complete" tool is not necessary: a reply in text ends the turn.

### Task 11: Find products by soft wishes

A lookup tool `similar_products(pick=None, wish)`: products that are near a pick or a wish ("like pick 2 but quieter", "a modern look"). Each product gets one vector from its chunks (the mean). The result also gives the reason (the best matching chunk of each product).

### Task 12: Rank by the shopper's wishes

The preferences in the needs ("quiet", "white") change the order of the picks. For each preference, a retrieval over the chunks of each candidate gives a score. The final score adds these scores to the rating score with fixed weights. The picks say which wishes moved a product up. Price stays out of the score.

### Task 13: Memory across conversations

A profile of the shopper: likes, dislikes (with the reasons from removed picks) and facts (for example the size of the kitchen). It is stored in Postgres for each shopper. The clarify step reads it. The shopper can see and delete it.

### Task 14: Evaluation with a simulated shopper

Method of InteRecAgent: take one stored product as the hidden target. A cheap model plays a shopper whose wishes come from the attributes and reviews of that product. It talks with the agent for at most 5 turns. Metrics: hit@5 (the target is in the picks) and the mean number of turns. This measures if the agent finds a product that fits the wishes, not if the product is the best. The run uses paid models, so Zheyun says when.

### Later, with Task 9

One researcher for each pick searches the web at the same time, and a cheap model makes the found pages short, with their URLs. This is the "conduct research" tool of open_deep_research.

## Not in this plan

- The web page for the chat (decision 5). It gets its own plan, together with FastAPI from phase 7. It runs on Zheyun's computer (localhost) first, because the dedicated browser window for amazon.se is there.
- PaddleOCR-VL for images (phase 4). `read_images` with the worker stays.
- Python's fixed messages in the shopper's language.
