# Phase 3 Text and Retrieval

> **How to use this plan.** I answered the questions on 2026-10-06 (see my decisions). Commit at the end of each task. Do not run a paid evaluation until I ask.

**Goal:** The agent finds the right text of many products and many reviews, not only the full text of one product. It uses hybrid retrieval (bge-m3 vectors, BM25 and RRF). It reads the shop reviews behind the login, finds attribute values that the database does not have, and writes pros and complaints in the picks. An evaluation compares the retrieval methods with the long-context baseline.

**Why:** On 2026-10-06 the database has 24 listings, 23 page texts and 114 reviews. The full text of one product fits in the prompt, so `read_product` works now. But the model cannot ask a question across all products ("which ones have reviews about noise") without reading each of them. With the shop reviews (about 10 reviews on each review page), the text becomes too large for the prompt.

**Spec:** sections 4.4 (text path, on-demand extraction), 4.5, 4.6, 4.7 (pros and complaints), 4.8 (`search_text`, `extract_attribute`), 7 (retrieval quality) and phase 3 in section 9.

**What exists now:**

- Postgres 17 with pgvector 0.8.7 in Docker. The extension is on, but no table has vectors.
- `page_texts` keeps specs, bullets, description, the reviews on the product page and the image links of each listing.
- `read_product` puts the full text of a pick into the prompt (at most 12000 characters). This is the long-context implementation of the spec.
- The fridge profile has `complaint_types`: noise, frost, door seal, compressor life, after-sales service.
- The GPU is an RTX 3060 Laptop with 6 GB. No torch and no embedding library are installed.

## My decisions (2026-10-06)

1. **Local model and libraries: yes.** torch with CUDA, FlagEmbedding and bge-m3 (from Hugging Face) go into the conda env and the Hugging Face cache. FlagEmbedding gives the dense vector and the sparse weights of bge-m3 in one call.
2. **Shop reviews: read the good and the bad ones.** The agent reads the positive and the critical review pages of each top pick (amazon.se has a star filter for each). When amazon.se is not logged in, the agent tells the shopper to log in in the dedicated browser window, and waits. Limits: the top 10 picks, at most 3 pages for each pick, at most 30 pages for each search.
3. **An LLM makes the evaluation set.** It writes about 30 questions from the stored text, marks the chunks that hold each answer and copies the exact words. Python checks that the words are in those chunks. The DeepSeek grader marks the answers of the chat model. The plan and any CV text say "made by an LLM, checked by Python", not "labelled by hand".
4. **Professional reviews: a free source for all categories.** She asked for a free source that covers all kinds of products, for example YouTube or a web search. See Task 9.
5. **A web page for the chat.** At the end, the shopper talks to the agent on a web page. This comes into the schedule, so that I can test the agent myself.

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

On 2026-10-06 I installed torch 2.6.0 (CUDA 12.4) and FlagEmbedding 1.4.2. The model download is about 4.3 GB, because the model repository also has an ONNX copy. `scout embed` gave the 181 chunks their vectors in 17.5 s on the RTX 3060. Chunk sizes are still counted as 4 characters for each token.


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

On 2026-10-06 I logged in, and I saved real review pages of the Bomann KG 320 outside Git. Findings:

- The review pages show the reviews of all Amazon shops of the EU. "From Sweden" had 0 reviews for this fridge. The other reviews are in German, Spanish, French, Dutch and Italian. bge-m3 is multilingual, so this is not a problem for the search.
- amazon.se ignores `pageNumber` in the URL. Page 2 gave the same 10 reviews as page 1. The "Show 10 more reviews" button loads more with a script. So the third page of a pick is the critical reviews sorted by `recent`. On the Bomann it gave 4 new reviews of 10.
- Each review has a stable id (for example `R2GOTWYJOAXP42`). The parser reads only the title, the rating, the date, the verified badge, the variant and the original text. It never reads the reviewer block or the hidden translation.

What was built:

- New table `shop_reviews`: one row for each review, with the filter that found it. Its chunks have the source `review_page` and the ref `shop review <id>`. A review that the product page shows too gets no second chunk.
- `scout.reviews.read_reviews`: for each pick, the top positive page, the top critical page, then the newest critical reviews when the filter has more than 10. Limits: 10 picks, 3 pages for each pick, 30 pages for each search. The table `review_reads` keeps the last read of each listing, also when it found no review. A complete read is not repeated for 7 days. When the shopper skips a login or a captcha, the step stops, and a read that the skip or the page limit cut short is repeated by the next search. The step runs only after a turn that showed new picks.
- The graph has a new step `reviews` after `recommend`. It reads the reviews of the picks that are shown (default 5), not of the top 10 candidates, because the state keeps only the picks. The web page now sends the replies of each step when the step ends, so the picks show before the reviews are read.
- `scout reviews ASIN...` and `scout parse-reviews PATH`. `scout chat` also prints the replies of each step when the step ends.

First real run (2026-10-06, 3 fridges): 9 review pages, 75 reviews (30 positive, 45 critical). 22 of them were on the product pages too, so they got no second chunk. The other 53 gave 54 chunks (one long review has two). `scout embed` gave 67 chunks vectors in 7.3 s: these 54 and 13 product page chunks from my web test. A Chinese question about noise ("冰箱噪音大吗") found two German reviews about a loud fridge in its top 2.

Plan as written before the build:

- Parser for the review pages of amazon.se: title, rating, date, verified purchase, variant, text. No author.
- Login check on each review page, from the page that was just fetched (no extra request). When the login is gone or a captcha shows, the agent tells the shopper to log in or solve it in the dedicated browser window, and waits. It never types a password.
- After the picks, the agent reads the positive and the critical review pages of the top 10, within the page limits. The reviews go into `page_texts` and into chunks. Each review keeps which filter found it.
- Step that needs me: I log in, and I save one review page as a test fixture outside Git. The parser is written against that page.

### Task 6: On-demand attribute extraction (done)

Built on 2026-10-06. On that day the stored fridges had few values for the conditions: height, width and depth on 7 of 22, total volume on 4, freezer volume on 2, noise on 3.

- `scout.extract.ondemand.fill_missing` runs in the picks after the title screening. It takes the first 15 candidates that miss a value of a condition. For each candidate it does one retrieval for all its missing attributes (5 chunks for each, at most 10), from the specs, description and image text of all listings of the product (not the reviews). One worker call reads all the missing values of the candidate, each with `raw` and `chunk_id`. The 15 places go only to candidates that still need a search.
- Python keeps a value only when the chunk id was given, the raw text is in that chunk, and the raw text shows the value with a unit word (the same check as the page extraction). The value goes into `products.attributes` with `source_chunk_id`, and also with the listing and the ref of the chunk, because chunk ids change when a newer page is stored. Then the size plausibility check, the conditions and the rank run again, so a found value can remove a candidate.
- The table `attribute_searches` records each search with an md5 hash of the text that it read, also when it found nothing. The same text is not searched again. A recheck of the same page and new review chunks do not change the hash. When the worker is not available, the values found before stay, and the note says that the search stopped early. An error of the text search does not stop the picks.
- `scout eval-chat` does not use it (`find_missing=False`), so its runs stay comparable with the earlier runs.
- Real run (2026-10-06, condition height up to 180 cm, Gemma 4 26B as the worker): 6 products searched, 3 values found, all raw texts in the cited chunks. Two were right (180 cm and 54 cm). The third was 1 cm, from "10D x 10B x 10H millimeter" on a 173 l Bomann. The page really says it, so the raw-text check passed.
- I first added plausible ranges to the profile for such values. I decided against it: the chat model can see this itself, so no Python rule. The ranges were removed. Test (2026-10-06): GPT-6 Luna wrote the picks for this fridge (height 1 cm) and the Bomann French Door (180 cm), with the usual prompt and no hint. For the 1 cm fridge it wrote "the unusually low height value may be a data issue". It did not doubt the other fridge. The Python conditions still count 1 cm as up to 180 cm, so the pick stays, with the remark of the model. One call, 0.0002 USD. The older Python rule of phase 1 (ignore height, width and depth when the box cannot hold the stated volume) was removed too, on my decision. The worker size check on each pick (phase 2 Task 11) stays, because it is a model judgement.

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
- Real run (2026-10-06, the 3 fridges with shop reviews, GPT-6 Luna, summaries in Chinese): all 3 passed the checks in the first round. Each pick got two pros and three complaints, qualitative, with mixed views stated (for example on noise). Each statement cited 1 to 6 reviews. My spot check: "dented on delivery" cites review 226, which says "Beschädigt geliefert". 3 calls, 0.0017 USD, 51 s.

Plan as written before the build:


- For each pick, one retrieval for each complaint type of the profile, over its review chunks.
- The chat model writes one or two pros and complaints for each pick. Each one cites chunk ids. Python checks that the ids exist and belong to that pick.
- The statements are qualitative, without counts (decided 2026-10-06), for example "a small part of the buyers say that it is noisy, most say that it is quiet". The picks show the source type of each statement (buyer review or test).

### Task 8: Retrieval evaluation (done)

- `eval/retrieval/questions.yaml`: about 30 questions in Swedish, English and Chinese. Some are about one product, some are across products, some name a model number. Each has the gold chunk ids and the exact words.
- `scout eval-retrieval`: for each method, recall@10, MRR, the tokens put into the prompt and the time. No API cost.
- ~~With my permission: the chat model answers each question with the top 10 chunks of each method and with long context.~~ I dropped this on 2026-10-13: it is not necessary now.
- `scout make-retrieval-questions` (2026-10-13): the chat model writes the questions from the stored chunks. 12 products get 2 questions each, in Swedish, English and Chinese, in three styles: with the model number, with the brand and the kind, and without a name. Their scope is the whole category, so the search must find the product first; within the chunks of one product (about 8) recall@10 would be near 1 for each method. 6 groups of 3 products get one question across them, with the reviews and specifications of the three as the scope. The model gives the number of each gold chunk and the exact words; Python keeps a label only when the words are in the chunk. The file stays out of Git, because it quotes the stored pages.
- The results go into this plan, with the size of the corpus. If the corpus is too small to show a difference, the plan says so.

**Results (2026-10-13).** The corpus: 553 chunks of 68 listings (379 review, 106 specs, 68 description chunks), most of them in Swedish. 30 questions made by Claude Haiku 5.5 and checked by Python (0.03 USD); 80 gold chunks. k = 10.

| Method | Recall@10 | Hit@10 | MRR | Tokens | Seconds |
|---|---|---|---|---|---|
| vector (bge-m3 dense) | 0.56 | 0.70 | 0.49 | 2416 | 0.20 |
| BM25 | 0.24 | 0.40 | 0.22 | 3268 | 0.06 |
| bge-m3 sparse | 0.28 | 0.43 | 0.25 | 2919 | 0.13 |
| vector + BM25 (RRF) | 0.51 | 0.67 | 0.34 | 2724 | 0.18 |
| multi-query, vector | 0.58 | 0.77 | 0.52 | 2476 | 0.39 |
| multi-query, vector + BM25 | 0.69 | 0.87 | 0.43 | 2677 | 0.64 |
| long context | 1.00 | 1.00 | 0.16 | 81950 | 0.01 |

- BM25 and the sparse weights match words, so they fail when the question is in another language than the text: for the English questions BM25 had a recall of 0.11, for the Chinese 0.05. The dense vectors of bge-m3 match across languages. So the first hybrid (one query, vector and BM25) was worse than the vectors alone.
- Multi-query: the question and its search words in Swedish, English and Chinese, each to vector and BM25, joined by RRF. It had the best recall and hit. For the Chinese questions the recall went from 0.30 (vector) to 0.78. Its MRR is lower than that of the vectors alone, but the chat model reads all 10 parts, so recall counts more.
- Long context has all gold chunks, but 34 times the tokens, and the gold chunks come late.
- I chose multi-query with vector and BM25 for `search_text`, because more shops in Chinese and English will come. The tool has a new field `more` for the search words in the other languages.
- Limits: 30 questions are few; one question changes a recall by about 0.03. The questions come from one LLM. 5 of the 6 questions across products are about noise. One model number in the data is a seller's code (Jectseo3ymfiqen6). One question without a name fits many products, but only one is gold. In the first set of questions each language had only one style, so language and style could not be told apart; the second set (above) has each style in each language.

### Task 9: Web search (decided 2026-10-06)

I decided the positioning on 2026-10-06 (spec section 1a). Web search finds models, tests, forum views and product pages of other shops. amazon.se stays the deep source. My terms decision covers the pages that web search finds.

- Tavily through its official API (free plan, 1,000 searches each month, no card). I make the account and put `TAVILY_API_KEY` in `.env`.
- Lookup tool `search_web(question)`. The searches use the model numbers of the picks and the needs of the shopper.
- The agent reads the found pages in parallel with several worker calls. Pages of one shop are still one at a time.
- Test articles and forum pages become chunks with the source `web`, the URL and the type (test or forum). Product pages of shops go through a generic reader: the schema.org `Product` JSON-LD first, then the worker with the raw-text check. They become listings, matched by GTIN when the page has one.
- The reviews on these product pages are buyer reviews. Reviews with the same text are kept one time.
- Tests: a fake Tavily client and saved pages; JSON-LD fixtures for the generic reader.

**Part 1 built on 2026-10-06 and 2026-10-07: `search_web`.** Part 2, `read_page`, follows below.

- `scout.websearch.TavilyClient` calls the REST API: a basic search (1 credit) with 20 results (my choice, 2026-10-06; the credit is the same for any number), the page text when Tavily has it, and the date of each page (`include_published_date`). Extract got the text of other pages (1 credit for each 5 pages that it gets) until our own fetcher replaced it (part 2). A status 401, 403, 429, 432 or 433 or no answer raises `WebSearchUnavailable`. Then the tool tells Luna, and the web floor is off for the rest of the turn.
- The tool `search_web(question, ids, want)`, in my steps (2026-10-07):
  1. Tavily gives up to 20 pages. In a test on 2026-10-07, only 4 of 20 came with their text; the others had a short part of about 150 characters.
  2. The worker judges each page from its title, address, date and short part, in parallel (10 threads): the kind (test, forum, shop, other), relevant or not, and which of the given products it is about.
  3. Extract gets the text of the relevant pages that came without it.
  4. The text is cut into parts of about 1,000 characters. The local reranker bge-reranker-v2-m3 scores each part against `want` (what Luna wants to learn, for example "is it quiet"), or else against the search words without the brands and the models of the given products. The 15 best parts go to Luna, each with the part before and the part after, at most 40 parts in all (about 10,000 tokens). Each page shows its site, kind, date and address. The other pages are listed with the reason of the worker.
  5. The relevant pages about a given product become chunks of that product, with the source `web`, the kind of the page, and the URL, the site, the title and the date in `meta`. Thus `search_text` finds them later. Pages that are not relevant keep only their title, address and short part.
- Why the rank and not a score threshold (the context filter of GPT Researcher, which I asked to follow). The test of 2026-10-07 with 83 real parts: the cosine of bge-m3 was high for menus that repeat the model name, and low (0.37) for buyer reviews about the noise that do not name it. The reranker ordered the parts well, but its scores did not fit a threshold: with the model name in the question nearly all parts of a page about the product scored above 0.9; without it all scored below 0.07. I agreed to the rank on 2026-10-07.
- The words of the search matter. A long English question ("Gorenje RK4182PS4 noise level, is it quiet? test and user reviews") found 13 manual and shop pages; 2 of 20 were relevant. "Gorenje RK4182PS4 test" found test sites in Germany, Slovakia and Denmark: 13 of 19 relevant. The system prompt now tells Luna to use short search words and to put the question in `want`.
- Real run (2026-10-07, "Gorenje RK4182PS4 test", want "is it quiet or loud", the free Gemini worker): 19 pages judged, 13 relevant, 11 stored, 38 parts to Luna (38,324 characters), 1 credit, no cost. The best part was an otto.de buyer review; testbericht.de "Der Betrieb ist angenehm leise" was among the 15. It took 81 s, because the free Gemini key allows 15 calls a minute; the reranker loads in about 26 s, one time in a session.
- The table `web_searches` keeps each question, the listings it was for, and the results (the relevant ones with their text, cut at 30,000 characters). The same question in the next 7 days uses them and costs no search credit. That use adds its products to the same row and does not change its date, so the results are not used after 7 days. A page that is stored again with the same text keeps its chunks and their embeddings.
- The floor of the design (section 5): before `show_picks`, each pick must have a web search with its id in the last 7 days, by any listing of its product. The shopper can switch it off (`update_needs` with `web_tests` false). Without a Tavily key there is no tool and no floor.
- A claim with the field `web` passes when the stored web pages about the product show its numbers. This check is weak: the pages are long, so a number can be found in a menu or a date. I decided on 2026-10-07 not to add a check by quote. The fact checks only record problems now.
- Limits for each turn: 120 web pages judged by the worker (6 searches of 20, so each of 5 picks can have its own search) and 10 web searches. The hidden-target test counts both.
- Not done yet: `summarize_reviews` reads only the shop reviews, not the web pages, and the picks do not show the source type of each statement yet.

Decisions of 2026-10-07 on the search back end:
- Pages that are not relevant keep their title, address and short part. A later search with the same words for other products can judge them again.
- I do not want to depend on Tavily. The search becomes a back end that can be replaced, as the LLM layer is. The fetcher of `read_page` (our own code) replaces Tavily Extract. A self-hosted SearXNG becomes a second search back end, and Tavily stays as the other one. The program can search Google through SearXNG (my decision, 2026-10-07; this replaces the finding of 2026-10-06 below). A captcha still stops a search; the program never bypasses it. `read_page` comes first, because both need its fetcher.

**Part 2 built on 2026-10-07: our own fetcher and `read_page`.**

- `scout.fetch.PageFetcher` loads a page with a plain HTTP request first. Pages of one site load one at a time, 3 seconds apart; pages of different sites load in parallel. When the page has less than 400 characters of visible text (it is built with JavaScript), the dedicated Chrome profile opens it. A refusal (for example 403 or 429), a captcha, a bot check or a login page stops the read, and the tool tells Luna that the page was not read. The browser is never used to get past them (code review, 2026-10-07).
- The fetcher replaces Tavily Extract in `search_web`. The text of web pages comes from trafilatura (an open source library, Apache 2.0). These pages load in worker threads, so they use plain requests only. Tavily Extract stays in the code as the fallback when no fetcher is given.
- The tool `read_page(url)` reads one product page of a shop that is not amazon.se (`scout.shops`, `scout.sources.generic`). The schema.org `Product` data of the page (JSON-LD) gives the title, the brand, the model (`mpn`), the GTIN, the price, the currency, the stock, the rating and the reviews. Then the worker reads the page text for the missing core fields and for the attributes of the category, with the raw-text check. The page text is the visible text without the menus, the header and the footer, because the specifications of shops are in tables and lists that trafilatura drops.
- The product joins a stored product with the same GTIN, or with the same brand and model. Spaces and hyphens in the model do not count. This also applies to amazon.se products now. A review with the same text as a stored review of the product in another shop is left out. The author of a review is never kept.
- I decided on 2026-10-07 that a product from another shop can be a pick. Its floors: its own `read_page` in this conversation counts as its shop search (not for the amazon.se picks); the reviews on its page count as its reviews; the live check opens its page again. With a budget, its price must be in SEK, because Python does not convert currencies; Luna can tell the shopper the price with a stated rate. A page with product data but no price (a test or a blog) is not stored as a shop listing. A link that leads to amazon.se goes to `search_shop`.
- Real check (2026-10-07, plain requests, no model, no cost). NetOnNet: title, brand, model, GTIN, price and stock from the page data; the price is in `priceSpecification`, next to the old price, which the reader leaves out. Elgiganten: the same fields; the specifications (height 192.1 cm, noise 41 dB) are in the page text. Power: the page data has the price and the rating, but the page is built with JavaScript, so the browser must open it for the specifications. The search pages of Elgiganten and Power need JavaScript; the product pages come from `search_web`.
- Not done yet: a real `read_page` turn with the worker (it needs OpenRouter credit).

**Part 3 built on 2026-10-07: SearXNG as the first search back end.**

- SearXNG runs in Docker next to Postgres (`docker compose up -d searxng`), image `searxng/searxng:2026.9.23-783a09462`, settings in `searxng/settings.yml`. It listens only on 127.0.0.1:8888, with the JSON format on. It asks Google, Bing, DuckDuckGo, Brave and other engines and gives their results. No key and no credits.
- `web_client()` gives the back ends in order: SearXNG first (`SEARXNG_URL`, by default the Docker service), then Tavily when `TAVILY_API_KEY` is set. When one is not available, the next one searches. `WEB_SEARCH=searxng`, `tavily` or `off` chooses.
- An engine that shows a captcha or limits the requests is left out by SearXNG. The program never bypasses it. When no engine answers, the search is not available, and the tool tells Luna.
- SearXNG gives only the short parts, so our own fetcher reads the text of every relevant page.
- After the code review (2026-10-07): SearXNG reads a second result page when the first gives fewer than 20 different addresses. All back ends give each address one time. When the engines are busy and nothing is found, Luna can try again; the second time in a turn, web search stops for the turn. A search that found nothing is not kept, so the same words search again. When SearXNG is not available and Tavily searches, the result says why, and the credit is counted. `scout eval-hidden` counts `web_credits` (Tavily credits) apart from `web_searches`. An unknown `WEB_SEARCH`, or `tavily` without a key, stops the start with a message.
- Real check (2026-10-07, no model): "Gorenje RK4182PS4 test" gave 20 results through Google (DuckDuckGo showed a captcha and Brave refused, so SearXNG left them out). Our fetcher read the text of 6 of 6 pages (testberichte.de, testbericht.de, testsieger.de, coolblue.de, tretti.se, appareils-menagers.net) in 2.6 s.
- The system prompt now tells Luna to look for a model that a test praises: `search_shop` with its brand and model, and `search_web` with the model and a word such as pris or köp for the pages of other shops. Such a model may not be sold in Sweden; then Luna says so (my request, 2026-10-07).

Findings from before the decision (2026-10-06): a Google search by the program is not an option (no automated searches, captchas). The YouTube Data API does not give transcripts. Brave Search needs a card.

## Additions of 2026-10-06

I read the comparison with two open-source agents and said to do all the proposals. The two agents are Microsoft InteRecAgent (a conversational recommender: query, retrieval and ranking tools, a plan made first, a critic, a user profile, a simulated user for evaluation) and LangChain open_deep_research (a supervisor with the tools think, conduct research and research complete; researchers that call tools by their own choice; limits on loops; a cheap model for summaries). We read their papers and documents only. We do not copy their code.

### Worker model (done)

On 2026-10-06 I tried 7 cheap OpenRouter models that read images and call tools. Task 1: keep the titles of the 24 stored listings that are a fridge with a freezer part (an LLM labelled 23 titles from the titles; one is unclear and not scored). Task 2: the colour and the number of doors of the FOHERE BC-90 from its 6 product images (black, one door).

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

Done on 2026-10-14. `Think(note)` returns "noted." and counts as one tool call. The web page shows the step as "Think it over", without the note. The note stays in the tool calls of the turn, so eval-hidden keeps it. The prompt tells the chat model to think before a conclusion, mainly before show_picks: list and count the candidates that fit, check each claim against its tool result (the site, the number, the variant), and note what is missing. This is for the most common fact faults of the Haiku run. Not measured yet.

On 2026-10-14 I stopped the paid grader for now, to save cost. I read the replies of a run myself. The flowcharts still show the grader, and `scout grade` stays.

### Task 11: Find products by soft wishes

A lookup tool `similar_products(pick=None, wish)`: products that are near a pick or a wish ("like pick 2 but quieter", "a modern look"). Each product gets one vector from its chunks (the mean). The result also gives the reason (the best matching chunk of each product).

Done on 2026-10-14. The tool is `similar_products(like, wish, more, limit)`. `like` is a product id, `wish` is the soft wish, and `more` is the wish in the other languages. The vectors of the wish in each language give one mean vector. Each listing of the category gets the mean of the vectors of its chunks (pgvector `avg`). The score adds the cosine similarity to the product and the cosine similarity to the wish. The listings of the product in `like` and the removed products are left out. The reason is the chunk of each listing that is nearest to the wish, without its title line. The result says that near is not the same as fit, and the prompt tells the chat model to check the needs before a near product is a pick.

Real check (bge-m3 on my computer, no model API, 65 fridge listings). "tyst kyl" with "quiet fridge" and "安静" gave 5 products that say quiet or tyst in their titles or reviews. "modern look, sleek design" gave weak results: a smart device for a fridge and a retro fridge are at places 1 and 2. A mean over all chunks of a product (mostly reviews) does not show the look well. Not measured with a test set yet.

### Task 12: Rank by the shopper's wishes

The preferences in the needs ("quiet", "white") change the order of the picks. For each preference, a retrieval over the chunks of each candidate gives a score. The final score adds these scores to the rating score with fixed weights. The picks say which wishes moved a product up. Price stays out of the score.

### Task 13: Memory across conversations

A profile of the shopper: likes, dislikes (with the reasons from removed picks) and facts (for example the size of the kitchen). It is stored in Postgres for each shopper. The clarify step reads it. The shopper can see and delete it.

### Task 14: Evaluation with a simulated shopper

Method of InteRecAgent: take one stored product as the hidden target. A cheap model plays a shopper whose wishes come from the attributes and reviews of that product. It talks with the agent for at most 5 turns. Metrics: hit@5 (the target is in the picks) and the mean number of turns. This measures if the agent finds a product that fits the wishes, not if the product is the best. The run uses paid models, so I say when.

On 2026-10-07 I removed `scout eval-chat` and its 24 scripts. They checked the routes of the fixed pipeline, and the agent loop has no fixed routes. `scout eval-hidden` replaces them. The grader (`scout grade`, `scout check-grader`) and the saved results of the earlier runs stay.

**First run with free models (2026-10-08).** One target (red-bottom-freezer), chat nemotron-3-ultra, worker nemotron-3-nano-omni, shopper nemotron-3-super, all free on OpenRouter, web search through SearXNG only. The target was at place 2 of 2 picks, in 2 shopper turns and 1259 s, at no cost. One run is not a result: it only shows that the loop works from end to end. What I changed after it:

- A reply without choices (OpenRouter gives status 200 with only an error when the model behind it fails) is retried like a busy server. The turn record of eval-hidden keeps the place in the code of an error.
- The amazon.se reader finds the brand and the model also on the English pages ("Brand Name", "Model Number"). Before, 7 of 9 products had no model.
- All 6 fact problems of the run were true values that the checks did not trace. Now the numbers of product codes (BC-46, KG 320) do not count, a rounded number counts (166 for 166.5), a web claim can take a number from the page of its product when the web pages show its other numbers, and the note of show_picks has its own claims (`note_claims`) for products that are not picks.
- eval-hidden counts what the gate stopped, by kind (shop floor, reviews floor, web floor, live check), and the turns that the gate gave up.
- I decided on 2026-10-08 that the grader checks the facts, not Python (agent-loop design, section 8). Luna gives no claims and no derived numbers. eval-hidden keeps the context and the tool calls with their results for each turn, and `scout grade` reads an eval-hidden results file. The test set of `scout check-grader` has the form of the loop now: the cards became the result of a tool call, any language is fine, and 6 new items are about page texts and web pages. `check-grader` reports the time of the grader, so I can choose a fast model.
- On 2026-10-11 I chose Claude Haiku 5.5 as the grader. `check-grader` on the 49 items: Haiku 5.5 found all planted faults and failed no correct reply (195 s, 0.04 USD). Gemini 3.8 Flash missed 1 fact and failed 1 correct reply (263 s, 0.25 USD). DeepSeek V4 Pro missed 1 fact and failed 1 correct reply (1319 s, 0.04 USD). Gemini 3.5 Flash Lite and the free Nemotron 3 Super missed 3 to 6 faults.
- On 2026-10-13 I changed the prompt of Luna after the first paid eval-hidden run (12 targets, 4 found, 4 stored but not picked). In 11 of 12 runs she asked no question, so she did not learn the budget or the must-haves. She left out a target that did not show its width and height. She used tests of another variant, said "only one fits" when more did, and gave an amazon.de price as an amazon.se price. The prompt now tells her to ask once before the first search, to look for a missing value before she leaves out a candidate, to use only tests of the same variant, to count the fitting candidates in the tool results, and to reply in the language of the last message.
- On 2026-10-13 I replaced the target frost-free-55 with frost-free-wide (a Bomann French door fridge, Total No Frost on its page). A buyer of the Candy says that it needs manual defrost, so Luna was right to leave it out. Then I ran eval-hidden again with the new prompt (Luna, SearXNG only). The target was in the picks in 7 of 12 runs (5 at place 1, 2 at place 2), before 4 of 12. 2 more were stored but not picked (skincare-mini, wine-24). The median run took 670 s and 0.014 USD; in all Luna cost 0.085 USD and the worker 0.102 USD. 8 runs used all 30 product pages of the turn. Luna still asked no question in any run, also when the opening gave no budget: the new prompt line did not work. Haiku 5.5 graded the 12 replies (0.08 USD): answers 12/12, facts 9/12, arithmetic 11/11, said_did 12/12, honest 12/12, plain 11/12.
- On 2026-10-13 I checked Luna as a grader, so that I can grade a run with Haiku as the chat model. `check-grader`: Luna missed 1 planted fact fault and failed 1 to 3 correct replies in facts, honest and plain (plain is not good enough). 358 s, 0.02 USD. Haiku stays the grader; a Luna grade of plain does not count.
- On 2026-10-13 I chose Claude Haiku 5.5 as the chat model and GPT-6 Luna Pro as the grader (config/models.yaml). In eval-hidden with the new prompt, Haiku asked for the missing budget of red-bottom-freezer before it searched; Luna asked a question in 1 of 24 runs. In the first 2 targets Haiku cost 0.041 and 0.045 USD for each run, Luna 0.012 and 0.021 USD. The grader must be of another maker than the chat model. `check-grader` with Luna Pro: all 37 planted faults found, 6 correct replies failed (facts 1, honest 2, plain 3), 665 s, 0.06 USD. So honest and plain of Luna Pro are not good enough; answers, facts, arithmetic and said_did count.
- The full eval-hidden run with Haiku 5.5 as the chat model (2026-10-13, same prompt and targets as the second Luna run): the target was in the picks in 10 of 12 runs (6 at place 1, 1 at place 2, 3 at place 3), Luna 7 of 12. 1 more was stored but not picked (dark-no-frost). Haiku asked first in 4 runs, each time for the missing budget or size, and the answers were good; once it asked in Swedish when the shopper wrote English. The median run took 617 s and 0.038 USD (Luna 670 s and 0.014 USD); in all Haiku cost 0.37 USD and the worker 0.10 USD. 6 runs used all 30 product pages.
- Luna Pro graded the Haiku run (0.14 USD): answers 13/16, facts 3/15, arithmetic 9/13, said_did 10/16, honest 5/16, plain 13/16. The fact faults are of three kinds: claims about all candidates that the results do not show ("only two fit", "no independent test"), wrong sources (testberichte.de for testbericht.de, two dates of two sites given to one), and small differences (41 cm for 41.8 cm, "dual-zone" for a fridge with a freezer). Many are real; some are too strict. These grades cannot be compared with the Haiku grades of the Luna run, because the grader is not the same.
- I decided that Luna judges if two listings are the same product. The GTIN and the same model still join them without Luna. When read_page stores a product that matches nothing, its report lists the stored products of the same brand from other shops, and Luna can call `same_product`. Example: amazon.se gives Bomann KG 320.2 the model number 732025.

### Later, with Task 9

One researcher for each pick searches the web at the same time, and a cheap model makes the found pages short, with their URLs. This is the "conduct research" tool of open_deep_research.

## Not in this plan

- The web page for the chat (decision 5). It gets its own plan, together with FastAPI from phase 7. It runs on my computer (localhost) first, because the dedicated browser window for amazon.se is there.
- PaddleOCR-VL for images (phase 4). `read_images` with the worker stays.
- Python's fixed messages in the shopper's language.
