# Free Choice, Product Text and More Tools

> **How to use this plan.** Zheyun asked for this on 2026-10-05: make the strict rules less strict, add the missing tools, and let the model answer without a tool when it chooses. Do not run `scout eval-chat` or `scout check-grader` until she asks. Commit at the end of each task.

**Goal:** The chat model decides more by itself and can read more. It can answer in text or call tools. It can read the full text, the reviews and the images of a product, and query all stored products. When no category fits, it drafts a new one.

**Why:** The review page of 2026-10-05 (https://claude.ai/artifact/RQFLNFbZoF99Mijy59occB) showed that the model saw only the product cards, that some checks rejected correct work, and that the category step did not show the kinds.

**Order of the spec.** These parts are in phases 3 and 4 of the spec. This plan builds the long-context version first: the full text of one product goes into the prompt. The spec names this version as the baseline for the retrieval evaluation. bge-m3, BM25 and RRF come later, and the evaluation compares them with this baseline.

## Decisions of Zheyun (2026-10-05)

- The model can answer without a tool. `tool_choice` is `auto`.
- The model can convert a currency. It tells the shopper the rate that it used.
- The model can reply in the shopper's language.
- Make the strict rules less strict and add the missing tools.

## Tasks

### Task 1: Free choice and less strict rules (done)

Also: a number in a reply that is on one of the cards needs no claim. Other numbers must be numbers of the shopper, pick numbers or derived numbers. The grader test set has two new correct items (a reply in Chinese, a conversion with its rate) and one new language fault. Python's own fixed messages ("Removed: ...", "Searching ...") are still in English.


- `tool_choice="auto"`. A reply with text and no tool call is the reply to the shopper. Python still applies the said-did check to it.
- The reply, the clarify question and the summaries use the shopper's language.
- `words` of `remove_picks` is kept as a record. Python does not check it against the shopper's message.
- Budget in another currency: the model converts it. `Needs.budget_note` keeps the amount, the currency and the rate. The budget check accepts a budget with a note.
- Conditions on soft attributes (for example `total_volume`) can come from the model's inference. Conditions on hard attributes (sizes) must still be numbers that the shopper wrote, because a wrong size means that the product does not fit.
- The category step shows the kinds of each category.
- Shipping and duty: the model still asks, because they depend on the seller.
- The grader rubric follows these decisions.

### Task 2: Store the product text (done)

On 2026-10-06, `scout backfill-text` stored the text of 23 of the 24 stored listings: 114 reviews and 284 image links. The reviewer names are not kept.

- New table `page_texts`: listing, fetch time, description, bullets, specs, reviews (title, rating, date, text), image URLs.
- The parser reads the description, the A+ text, the reviews on the page and the image URLs.
- `save_record` stores the text with each new fetch. `scout backfill-text` reads the newest snapshot of each stored product.

### Task 3: Tools that look things up

Lookup tools return their result to the model, and the model continues in the same turn (at most 3 lookups).

- `read_product(picks, question)`: the full text and the reviews of the picks.
- `query_products(kind, conditions, budget, limit)`: stored products of the category that match, as cards.
- `read_images(pick, question)`: the worker model reads the product images and answers the question. Prices never come from images (spec 4.4).

### Task 4: Draft a new category

When no category fits, the chat model drafts a category profile (kinds, attributes, questions, Swedish search words). Python validates it and saves it in `data/profiles/` (not in Git). The shopper is told that the category is a draft.

## Not in this plan

- bge-m3, BM25, RRF and the retrieval comparison (phase 3).
- PaddleOCR-VL (phase 4). The worker reads images.
- Reviews from the review pages, which need a login (phase 3). This plan uses the reviews on the product page.
