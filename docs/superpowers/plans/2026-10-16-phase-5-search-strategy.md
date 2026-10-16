# Phase 5 Search Strategy

> **How to use this plan.** I read this plan on 2026-10-16 and changed decisions 2 and 6. Commit at the end of each task. Do not run a paid evaluation until I ask.

**Goal:** The agent reads the right product pages first. The search uses the filters of amazon.se for the conditions of the shopper, and the order of the candidates comes from the needs, not only from the sort orders of the shop.

**Why:** On 2026-10-14 eval-hidden found 1 of 4 targets. In the 3 misses the target was not stored. On 2026-10-16 I saved the search pages of these runs again (19 pages, no model) and found where the targets were:

| Target | Candidates in the budget (page 1 of 3 sort orders) | Place of the target | Calls of 8 product pages to reach it |
|---|---|---|---|
| under-counter-quiet | 53 | 31 | 4 |
| retro-red | 107 | 86 | 11 |
| tall-top-freezer | 34 | not there | none |

- The targets are in the search results. The read order puts them late. The order now is the default, rating and sales orders of the shop, taken in turns (`interleave`). It does not use the needs.
- tall-top-freezer is not a valid target now. Its price was 5916 SEK on 2026-10-06 and is 7702 SEK on 2026-10-16, over the budget of 6500 SEK.
- The search page of amazon.se has filters in the URL (the `rh` parameter): price (any range), height, width and depth (in steps), configuration (freezer on top, at the bottom, side by side), defrost (Frost Free), installation (built-in, undercounter, freestanding), colour, features (Low Noise) and capacity. The names of the options are in the page (`aria-label` "Apply the filter Red to narrow results"). The ids were the same in the 3 searches.
- A test with filters (2026-10-16, no model):
  - under-counter-quiet, price at most 3200 SEK and height up to 85.9 cm: 40 results, the target at place 10 (before: 31).
  - retro-red, price at most 6500 SEK and the colour Red, words "retro kylskåp med frys": 48 results, the target at place 13.
  - With the word "röd" in the search words as well, the target was not in the results. Its title is German ("Root"). So a filter is safer than a word for a colour or a type.

**Spec:** section 4.3 (search strategy) and phase 5 in section 9.

## Decisions

1. **Filters from the page, not fixed in the code.** Python reads the filter groups and options from a search page and keeps them for each source and category. Thus a new category gets its filters without new code.
2. **Python sets the number filters.** For each hard condition of the needs (height, width, depth) Python selects all steps that overlap the allowed range. Example: width at most 62 cm selects "up to 47.9", "48 to 60.9" and "61 to 73.9". So a product of 61.5 cm stays in. Price uses the budget as an exact range. The match between a profile attribute and a filter group is by name ("Height" and `height`), set in the profile when the names differ. **Python also keeps its own check of the number conditions** (my decision, 2026-10-16), because other shops can have no filters. It applies the budget and each hard condition to the values on the search cards (the price, and the sizes that the title gives, for example "H x W x D: 180 x 55.6 cm"). A card with a value outside the condition is left out (outcome `off_condition`); a card without the value stays. This check runs for each shop, with or without filters, so a filter that the shop applies wrongly is caught too.
3. **The chat model chooses the word filters.** The result of search_shop lists the word filters of the page (colour, configuration, defrost, installation, features). The chat model can give some of them in the next search_shop call. Python checks that each one exists on the page. This follows the rule that the model judges the words.
4. **Filters can drop good products.** A product without the tag on amazon.se is not in a filtered list. Thus the filtered orders come first, and the unfiltered default order comes after them in the read order. A product that is only in the unfiltered list can still be read.
5. **The worker orders the candidates.** The title screen exists. It now also returns the kept numbers in order, the best match to the needs first. It gets the full needs (kind, conditions, budget, preferences), not only the kind. Many titles have the sizes and the colour, so this order is useful before a product page is read. When the worker fails, the order of the shop stays.
6. **The Threshold Algorithm is in v1** (my decision, 2026-10-16, for the same reason as decision 2: other shops and other lists come later, and the code must be ready for them). It decides how many result pages of each list to read. The lists are the sort orders that a shop has, each with a score that falls down the list: the rating order gives the Bayesian rating of the card, the sales order and the default order give a score from the place in the list. The total score of a candidate is the sum of its list scores; a candidate that is not yet seen in a list gets the score of the last card read in that list (the threshold). The search stops when k candidates (k = 10) have a total score at least as high as the threshold, or at the page limit. On amazon.se the algorithm mostly stops after page 1 of each list. Its full value comes with lists that cost more to read. The read order of the product pages still comes from the worker (decision 5).

## Tasks

### Task 1: Read the filters of a search page

- `parse_filters(html)` in `sources/amazon_se/search.py` gives the groups, and for each group its options: the name, the `rh` value and, for a number group, the range in its unit ("Up to 47.9 cm", "48 to 60.9 cm", "112 cm & above").
- A table `site_filters` keeps them for each source and category, with the date. A search page newer than 7 days updates them.
- Tests with saved pages in `tests/data` (the HTML of the refinements only, no product data).

### Task 2: Filters in the search URL and in the snapshot key

- `search_url(query, page, sort, filters)` adds `rh`. Options of one group are joined with `|`, groups with `,`.
- `snapshot_key` includes a short hash of `rh`, so filtered and unfiltered pages are kept apart.

### Task 3: Number filters from the needs

- `number_filters(needs, profile, filters)`: the price range from the budget, and the overlapping steps of each hard condition. An open condition (for example only "at least 60 l") selects the steps that overlap it as well.
- A hard condition without a matching group gives no filter.
- Tests: width at most 62 cm, height at most 86 cm, height between 170 and 180 cm, a budget.

### Task 4: search_shop with filters

- New argument `filters`: a list of word filters as "Group: Option", for example "Colour: Red". Python checks each one against `site_filters` and answers with the valid list when one does not exist.
- The crawl reads the filtered default, rating and sales orders on page 1, then the unfiltered default order. The read order puts the filtered orders first (decision 4).
- The result text names the filters that were used and lists the word filters that the page has, at most 10 for each group.
- The `unread` key and the `search_log` include the filters.
- When no filter data exists yet for the category, the first search reads the unfiltered page 1 as now, stores the filters, and the next call can use them.

### Task 5: The worker orders the candidates

- `screen_titles` returns the kept candidates in the order of the worker. The prompt gets `Needs.text()` with the kind, the conditions, the budget and the preferences.
- `read_details` reads in this order. `report.outcomes` stays the same.
- Tests with a fake worker: the order of the reads follows the worker; a worker error keeps the order of the shop.

### Task 6: The Threshold Algorithm

- `threshold_search(lists, k, max_pages)` in `scout/search/threshold.py`, with no shop code in it. A list gives its cards page by page and a score for each card.
- The amazon.se adapter declares its lists: the default order, the rating order and the sales order, each with and without the filters.
- Tests with fake lists: it stops when k candidates reach the threshold, it stops at the page limit, a list that does not load is left out.
- The result text of search_shop says how many pages of each list were read and why the search stopped.

### Task 7: A free search evaluation

- `scout eval-search`: for each target of `eval/hidden/targets.yaml`, it runs the searches with fixed words and needs (new fields `search_words` and `needs` in the target), and gives the place of the target in the read order, with and without the filters. It loads search pages only and uses no model, so the worker order of Task 5 is not in it.
- Before the search, it loads the product page of the target and checks the price against the budget and the stored sizes against the hard conditions. A target that does not fit any more is marked "not valid now" and is not counted.
- New field `budget` in each target. tall-top-freezer gets a new product in the budget of 6500 SEK (my decision, 2026-10-16). An LLM writes its opening and facts from the stored page, as for the other targets, and the hidden words are its brand and model.
- Report: for each target, the place without filters, the place with filters, and the calls of 8 product pages to reach it. Saved in `eval/results/search-<date>.json`.

### Task 8: eval-hidden with the new search (paid, after my OK)

- The same 4 targets as on 2026-10-14, then all 12 when the 4 look good.
- The funnel stages show where each target stops.

## Not in this plan

- Filters for other shops than amazon.se.
- Page limits and a money limit stay as they are (30 product pages for each turn).

## Progress (2026-10-16)

- Tasks 1 to 7 are done: `parse_filters` and the table `site_filters` (45264ec), the filters in the search URL and the snapshot key (8f44ca8), the number filters and the card check (c15893b), the Threshold Algorithm in the crawl (d3e7fd6), search_shop with filters (761dbbd), the worker order (9f359d5), `scout eval-search` (948ebf8) and the new product of tall-top-freezer (a117a07).
- The amazon.se pages show only the options that have results for the search words. For example, the height step "86 to 111.9 cm" was not on one page. So `site_filters` merges the options of all pages.
- The sizes in a title are read only with a word next to them ("170 cm High", "Height 180 cm", "54 cm breit"). "144 x 47.5 x 49.5 cm" has no fixed order: some titles give H x W x D, others W x D x H. On 612 cards of 2026-10-14 and 2026-10-15, the check left out 16 cards for a height of at most 180 cm and a width of at most 56 cm, and all 16 were right. Wrong values exist ("72 mm High" for a fridge of 183 cm); they can only leave out a card for a condition "at least".
- New product of tall-top-freezer: the Candy CCG1S 518EW (B0CTKNJJGG), 5489 SEK, 179.5 x 54.5 x 54.6 cm, 252 l, freezer at the bottom, 41 dB, 331 ratings. I did not use the FrigeluX RC262XE: it fits, but the chat model (Haiku 5.5) picked it in the run of 2026-10-14, so it would favour the agent. Its page also says "Freezer-on-Top" while its pictures show the freezer at the bottom.

### eval-search after the review fixes (2026-10-16, no model, about 15 minutes of page loads)

The code review found that the first run used the order of the Threshold Algorithm as "before", not the search of 2026-10-14, which took the orders in turns. That run said 5 of 12 before and 9 of 12 with filters. The second run, 20 minutes later, has three rows. "before" is page 1 of the default, rating and sales orders, taken in turns, with the budget: the search of 2026-10-14. "without filters" is the same pages in the order of the Threshold Algorithm. "with filters" is the new search without the worker order. The word filters are my choice from the facts, as the chat model would choose them.

| Target | before | without filters | with filters |
|---|---|---|---|
| red-bottom-freezer | 5 of 24 | 1 of 24 | 2 of 21 |
| tall-top-freezer | not in 34 | not in 34 | not in 26 |
| frost-free-wide | 5 of 54 | 2 of 54 | 1 of 30 |
| retro-red | 4 of 119 | 10 of 119 | 2 of 108 |
| dark-no-frost | 12 of 46 | 20 of 46 | 2 of 37 |
| narrow-cheap | 37 of 45 | 26 of 45 | 7 of 32 |
| side-by-side | 6 of 52 | 3 of 52 | 2 of 31 |
| under-counter-quiet | 43 of 59 | 4 of 59 | 12 of 55 |
| skincare-mini | 3 of 41 | 14 of 41 | 2 of 86 |
| wine-24 | 1 of 80 | 2 of 80 | 4 of 100 |
| small-freezer-or-fridge | not in 103 | not in 103 | not in 168 |
| car-compressor-box | not in 62 | not in 62 | 88 of 156 |

- In the first 8 product pages (one search_shop call): before 6 of 12 targets, without filters 5 of 12, with filters 8 of 12.
- The order of the Threshold Algorithm alone is not better than the old order. The gain comes from the filters.
- The results of amazon.se change within minutes: retro-red was at place 16 before in the first run and at place 4 in the second, under-counter-quiet at 4 and at 43. One run of 12 targets is a small sample. The difference of 6 and 8 targets can be noise; the targets that moved most (dark-no-frost, narrow-cheap) moved in both runs.
- under-counter-quiet is at place 12 with filters: the height filter keeps many small fridges, and their order puts others first. The worker order, which this evaluation does not have, reads the titles for the sizes and the wishes.
- The 3 misses are not in the results of their words (car-compressor-box only at place 88, on the second page of the filtered lists). Their cause is the search words. In the agent, the chat model can search again with other words.
- The Threshold Algorithm stopped after page 1 of each list for most targets (4 search pages).
