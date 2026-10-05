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

### Task 5: Shop reviews behind the login

- Parser for the review pages of amazon.se: title, rating, date, verified purchase, variant, text. No author.
- Login check on each review page, from the page that was just fetched (no extra request). When the login is gone or a captcha shows, the agent tells the shopper to log in or solve it in the dedicated browser window, and waits. It never types a password.
- After the picks, the agent reads the positive and the critical review pages of the top 10, within the page limits. The reviews go into `page_texts` and into chunks. Each review keeps which filter found it.
- Step that needs Zheyun: she logs in, and Claude saves one review page as a test fixture outside Git. The parser is written against that page.

### Task 6: On-demand attribute extraction

- When the shopper sets a condition on an attribute that a candidate does not have (for example "not taller than 180 cm"), the agent retrieves the chunks about that attribute for that candidate.
- The worker extracts the value with `raw` and `source_chunk_id`. Python checks that `raw` is in that chunk. If not, the value is missing and the picks say so.
- The value goes into `attributes` in the spec format.

### Task 7: Pros and complaints in the picks

- For each pick, one retrieval for each complaint type of the profile, over its review chunks.
- The chat model writes one or two pros and complaints for each pick. Each one cites chunk ids. Python checks that the ids exist and belong to that pick.
- The picks show the source type (buyer review in v1) and how many reviews say it, when the model counts them.

### Task 8: Retrieval evaluation

- `eval/retrieval/questions.yaml`: about 30 questions in Swedish, English and Chinese. Some are about one product, some are across products, some name a model number. Each has the gold chunk ids and the exact words.
- `scout eval-retrieval`: for each method, recall@10, MRR, the tokens put into the prompt and the time. No API cost.
- With Zheyun's permission: the chat model answers each question with the top 10 chunks of each method and with long context. The DeepSeek grader marks each answer. The report gives accuracy, tokens, cost and latency for each method.
- The results go into this plan, with the size of the corpus. If the corpus is too small to show a difference, the plan says so.

### Task 9: Reviews from the web (needs a decision)

Findings of 2026-10-06:

- A Google search by the program is not an option. Google does not allow automated searches, and it shows captchas. The program never bypasses captchas.
- YouTube: the official YouTube Data API is free (a daily quota), but it does not give the transcripts of other people's videos. Tools that read transcripts use pages that are not an official API.
- A search API made for agents fits best. Tavily has a free plan of 1,000 searches each month, with no credit card. It also gives the text of the found pages. Brave Search now needs a credit card, so it is not an option (never enable billing).
- Swedish sites such as test.se and testexperterna.se collect tests for many categories. Many such sites are lists for affiliate links, so their quality varies.

Proposal: a lookup tool `search_web(question)` that searches for "model number + test or review" through Tavily. The results are chunks with the source type "web" and their URL, and the picks show them as web sources, not as professional tests. Zheyun makes the Tavily account and puts the key in `.env`.

## Not in this plan

- The web page for the chat (decision 5). It gets its own plan, together with FastAPI from phase 7.
- PaddleOCR-VL for images (phase 4). `read_images` with the worker stays.
- Python's fixed messages in the shopper's language.
