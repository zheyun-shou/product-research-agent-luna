# Review sources outside amazon.se

Web search already finds tests and forum threads, but only by chance: the search words decide which pages come. This plan adds three kinds of sources on purpose, with a list of sites for each: buyer reviews of other Swedish shops, test sites, and forums. My choice of 2026-10-20. The terms decision of 2026-10-06 (spec section 8) covers them: personal, low-volume access to the pages that web search finds.

## What I checked on 2026-10-20 (no model, a few page loads)

| Site | What a plain request gets | Reviews |
|---|---|---|
| Prisjakt | the product page; the reviews come from `POST /api/products/{id}/reviews` with `{"pageSize": n}` | yes, with the market (SE, NO) and the rating. Some are about the shop, not the product |
| Elgiganten | the product page; the reviews come from the Feefo widget (`api.feefo.com/api/10/reviews/product`, merchant `elgiganten-sweden`, the article number as sku) | yes. Feefo mixes in reviews of Elkjøp in Norway |
| NetOnNet | the reviews are in the page data (JSON-LD, Bazaarvoice) | yes, 5 on the page |
| Power | the rating and the count in the page data (4.5 of 5, 854 ratings) | no text: it did not load in the browser either |
| SweClockers forum | the thread text | yes, page by page (`?p=2`) |
| Flashback | a page that checks the browser ("JavaScript krävs"), status 403 | no: a bot check stops the read, and the browser is never used to get past it |
| Reddit | an empty page; the `.json` address gives 403 | no: only through the official API with a key of my own |

`site:` works through SearXNG, also several sites with OR in one search ("(site:a OR site:b)").

## Design

- `config/review_sources.yaml` lists the sites for each kind (shops, tests, forums), with more test sites for each category. I edit it.
- search_web gets `sources`: any (as before), tests, forums or shops. Python adds the sites of that kind to the search words with OR. A forum of the list that blocks reading (Flashback, Reddit) still gives its short part in the search result.
- read_page reads more reviews on the sites that load them apart from the page: Prisjakt (its review API, the reviews of all markets with the market noted) and Elgiganten (Feefo). The other shops keep the reviews of the page data. The same text in two shops is kept once, as before. Prisjakt is a price comparison site: its price is the lowest offer, and the report says so.
- A forum thread of SweClockers is read with its next pages, at most 3 pages.
- The candidate researcher gets read_page (without the browser, because the researchers run at the same time) and same_product, both only for its own product, and at most 3 product pages each. Its prompt tells it to look for tests, forum views and the buyer reviews of other Swedish shops.
- The main agent's prompt names the sources of search_web.

## Not in this plan

- Reddit through its official API. It needs an app and a key that I make myself. I decide later.
- Flashback.
- The reviews of Power.

## Progress (2026-10-20)

- Built: `config/review_sources.yaml` and `review_sources.py`, the `sources` of search_web, `shop_reviews.py` (Prisjakt and Feefo), the next pages of a forum thread (`websearch.thread_pages`, at most 2 more pages, the same address with `?p=N` or `?page=N`), and read_page and same_product for the researchers. A researcher gets at most 3 web searches (tests, forums, shops) and 3 product pages.
- The reranker does not see the sites of the search words ("site:..." and "OR" are taken out of the topic).
- A real check without a model (2026-10-20), read_page into the test database: the Prisjakt page of the AirPods Pro 3 stored 27 buyer reviews (all of them), the Elgiganten page of the Gorenje RI4182E1 stored 4 from Feefo, and the NetOnNet page of the Sony WH-1000XM6 stored 5 from its page data.
- Not run with a model yet.

### Code review (2026-10-20)

The review found 10 problems. 7 are fixed, and 3 are known limits.

- The next pages of a thread are read only on the forums of the list and only from the first page. On Prisjakt, `?p=` is a product, so the first version read other products as pages of the thread. The next page loads only while the text is shorter than the limit.
- A search of shops or forums only does not count as a web search for tests (the web floor).
- read_page loads the page, the reviews of Prisjakt or Feefo and the worker reading without the database lock. It takes the lock only to store. So a researcher can read a shop page while another one reads the database.
- An unknown answer of Prisjakt or Feefo does not stop the read: the page data still counts. Their requests keep the gap of 3 seconds of the site, also the first one after the page.
- same_product of a researcher runs only when one of the two ids is its own product, and never for one id with itself.
- The reranker no longer loses the words "OR" and the brackets of the shopper; only the group of sites at the end is taken out.
- Known limits: (1) the review requests of Prisjakt and Feefo are not in the fetch log; (2) a researcher reads shop pages without the browser, so a page that needs JavaScript gives only its page data (the main agent can read it again with the browser); (3) the test sites are listed by the names of the preset categories, so a drafted category with another name gets only the sites for all categories. Tavily, the second back end, may not follow "site:" with OR; SearXNG does.
