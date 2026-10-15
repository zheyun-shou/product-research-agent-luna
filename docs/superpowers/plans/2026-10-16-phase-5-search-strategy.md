# Phase 5 Search Strategy

> **How to use this plan.** I read this plan before the work starts. Commit at the end of each task. Do not run a paid evaluation until I ask.

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
2. **Python sets the number filters.** For each hard condition of the needs (height, width, depth) Python selects all steps that overlap the allowed range. Example: width at most 62 cm selects "up to 47.9", "48 to 60.9" and "61 to 73.9". So a product of 61.5 cm stays in. Price uses the budget as an exact range. The match between a profile attribute and a filter group is by name ("Height" and `height`), set in the profile when the names differ.
3. **Luna chooses the word filters.** The result of search_shop lists the word filters of the page (colour, configuration, defrost, installation, features). Luna can give some of them in the next search_shop call. Python checks that each one exists on the page. This follows the rule that the model judges the words.
4. **Filters can drop good products.** A product without the tag on amazon.se is not in a filtered list. Thus the filtered orders come first, and the unfiltered default order comes after them in the read order. A product that is only in the unfiltered list can still be read.
5. **The worker orders the candidates.** The title screen exists. It now also returns the kept numbers in order, the best match to the needs first. It gets the full needs (kind, conditions, budget, preferences), not only the kind. Many titles have the sizes and the colour, so this order is useful before a product page is read. When the worker fails, the order of the shop stays.
6. **No Threshold Algorithm in v1.** All sort orders of amazon.se show the rating and the review count on the cards, so a second list costs nothing to read. The algorithm has a value after v1, when a list (for example review mentions) is expensive to read. Section 4.3 of the spec gets this note.

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

### Task 6: A free search evaluation

- `scout eval-search`: for each target of `eval/hidden/targets.yaml`, it runs the searches with fixed words and needs (new fields `search_words` and `needs` in the target), and gives the place of the target in the read order, with and without the filters. It loads search pages only and uses no model, so the worker order of Task 5 is not in it.
- Before the search, it loads the product page of the target and checks the price against the budget and the stored sizes against the hard conditions. A target that does not fit any more is marked "not valid now" and is not counted.
- New field `budget` in each target. I fix tall-top-freezer: a new budget or a new product (my choice when the task starts).
- Report: for each target, the place without filters, the place with filters, and the calls of 8 product pages to reach it. Saved in `eval/results/search-<date>.json`.

### Task 7: eval-hidden with the new search (paid, after my OK)

- The same 4 targets as on 2026-10-14, then all 12 when the 4 look good.
- The funnel stages show where each target stops.

## Not in this plan

- The Threshold Algorithm (decision 6).
- Filters for other shops than amazon.se.
- Page limits and a money limit stay as they are (30 product pages for each turn).
