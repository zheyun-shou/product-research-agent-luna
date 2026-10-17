# Phase 4 Images and More Categories

> **How to use this plan.** I read this plan on 2026-10-17 and accepted the proposal of each decision. Incense is for a buyer. Commit at the end of each task. Do not run a paid evaluation until I ask.

**Goal:** The agent works in more than one category. It has three more preset categories, it drafts a usable profile for a category that has none, and it reads facts that only the product images show.

**Why:** product-scout is an agent for any category, but all tests and all 12 hidden targets are fridges. Thus I can show only that it works for one category. The incense category is also one of the two real uses (the possible shop).

**Spec:** sections 4.1 (category profile), 4.4 (text path, images) and phase 4 in section 9.

## What exists now

- One preset profile: `profiles/fridge.yaml`. Its kinds, attributes, title words and site filter names are written by hand.
- `draft_profile` (agent/clarify.py) lets the chat model draft a profile. Python checks its form. Only the old graph uses it. The agent loop rejects a category without a profile ("'X' is not a category").
- One draft from 2026-10: `data/profiles/dishwasher.yaml`. It has "dimensions" as one text attribute of kind hard. Python cannot check a text against a size, so this draft cannot filter by the space of the shopper.
- `read_images` (agent loop) sends up to 6 images of one product to the worker with one question. The answer goes to the chat model only. It is not stored, so the next question about the same image costs a new call.
- PaddleOCR-VL is not installed. The environment has torch and transformers. The GPU is an RTX 3060 Laptop with 6 GB.

## Search pages of the new categories (2026-10-16, no model)

I saved page 1 of three searches and read their filters with `parse_filters`.

- **"trådlösa hörlurar"**: many useful filters. Form factor (In Ear, On Ear, Over Ear, True Wireless), noise control (Active Noise Cancellation and others), connectivity (Wired, Wireless), water resistance, colour, brand. Number filters exist only for sensitivity and charge time. The titles often give the battery time ("Up to 50 Hours Battery Life", "40T Playtime").
- **"löparskor dam"**: filters for gender, women's and men's shoe size (EU, in half sizes), surface (Road, Trail, Treadmill), arch type, water resistance, colour and brand. The card title has two `h2` elements, the brand and the name ("ASICS" and "Women's Patriot 14Sneaker"). The parser reads only the first, so all titles are a brand only. This must be fixed before shoes can work.
- **"rökelse"**: the filters are for heaters (heating method, heat output), and the results mix incense with holders and cutters. The titles give the weight or the count ("50 g", "320 Sticks", "120 Pieces Incense Cones") and the form (sticks, cones, backflow cones, resin, wood). Better search words per kind ("rökelsepinnar", "rökelsekoner") must be tested.

## Decisions (2026-10-17)

1. **The three preset categories.** The spec says incense, headphones or small electronics, and clothes or shoes. I chose incense, headphones and running shoes. Running shoes have a clear hard condition (the size) and real filters.
2. **Shoe sizes.** The price and the stock of a shoe depend on the size. In v1 the size is a hard condition. Python reads the size list of the product page, and a pick must have the size of the shopper in stock. The price shown is the price of that size when the page gives it, otherwise the page price with a note.
3. **Incense as a buyer or a seller.** On amazon.se the agent helps a buyer. A seller wants other facts (demand, review count, price per gram, the supplier). In v1 the profile is for a buyer, with the price per gram or per stick as a fact that the chat model calculates. The seller view comes with the shop work after v1.
4. **Draft profiles learn the filters of the shop.** After the first search of a new category, Python matches the attribute labels of the draft with the filter group names of the page (the same match as for presets). The chat model drafts the title words. Sizes must be separate numbers (height, width, depth), never one text. A draft stays marked `draft: true`, and the chat model tells the shopper that the category is new.
5. **PaddleOCR-VL for images, the worker as the fallback** (as in the spec). The image text goes into the chunks with the source type `image`, so search_text finds it and no image is read twice. Before I build it, a short test: does PaddleOCR-VL run in 6 GB, and how long does one image take? If it does not run, the worker reads the images and its text goes into the chunks the same way.
6. **The image labels.** About 15 product images with specifications (size drawings, spec tables, energy labels), across the four categories. The labels are drafted from the images, and I check each one. The images and the labels stay out of Git.

## Tasks

### Task 1: Card titles in two parts

- `parse_search_page` joins all `h2` texts of a card ("ASICS Women's Patriot 14Sneaker").
- A hand-written fixture with a two-part title, and the fridge fixtures still pass.

### Task 2: The headphones profile

- `profiles/headphones.yaml`: kinds (in ear, over ear, on ear, sport, kids), attributes (form factor, wireless, noise cancelling, battery time in h, water resistance, weight in g), questions and complaint types.
- `site_filter` names where the label is not the filter group name. `title_words` for the battery time.
- Check the profile against 2 or 3 saved search pages: which conditions get a filter, and how many card titles give the battery time.

### Task 3: The incense profile

- `profiles/incense.yaml`: kinds (sticks, cones, backflow cones, resin, wood, holders), attributes (form, scent family, count or weight, burn time in min, natural ingredients, origin), questions and complaint types.
- Test the search words of each kind on amazon.se (no model) and keep the words with the fewest results off topic.

### Task 4: The running shoes profile and the size condition

- `profiles/running_shoes.yaml`: kinds (road, trail, treadmill, kids), attributes (EU size as a hard condition, gender, surface, drop in mm, weight in g, waterproof), questions and complaint types.
- Read the size list and its stock from saved product pages (check first how amazon.se shows them). A pick without the size of the shopper in stock is left out, with the reason.

### Task 5: New categories in the agent loop

- `update_needs` with a category that has no profile drafts one with `draft_profile`, saves it to `data/profiles/`, and returns the profile text. The draft counts as one model call in the turn limits.
- The draft prompt asks for separate number attributes for sizes and for title words. `check_draft` rejects a hard attribute of type text.
- After the first search, Python adds the `site_filter` matches to the draft (decision 4).
- The context lists the draft categories with "(draft)".
- Tests with a fake model: a new category, a draft with a text size (rejected, then fixed), and a second conversation that loads the saved draft.

### Task 6: The shopper edits a profile

- A tool `update_profile` (spec section 4.8): add an attribute, remove one, or change hard and soft. Python checks the form, as for a draft. The edited version is saved as a new file in `data/profiles/` and marked `edited`. A preset file in Git is never changed by the agent.

### Task 7: Image text with PaddleOCR-VL

Only after the test of decision 5.

- `scout/extract/images.py`: download the images of a product from the image server of amazon.se only, cut a long image into segments, read each with PaddleOCR-VL, and give the markdown. When the text is empty or not usable, the worker reads the image.
- The text goes into the chunks of the listing, with the source type `image` and the image URL. A price in image text is never used.
- `read_images` reads the stored image text first and reads the images only when there is none.
- `scout ocr-ping` reads one saved image and prints the time and the memory used.

### Task 8: Image evaluation

- `scout eval-images`: about 15 labelled images (decision 6). For PaddleOCR-VL and for the worker: the share of labelled values found, the time and the cost.

### Task 9: Hidden targets of the new categories and a free search evaluation

- 3 targets in each new category (9 in total), made the same way as the fridge targets: a real product of amazon.se in stock, an opening, the facts, the hidden words and a search case.
- `scout eval-search` on them, no model.

### Task 10: Draft profile against preset profile

- For each new category, the chat model drafts a profile from the opening of one target (about 3 model calls, under 0.01 USD, after my OK).
- `scout eval-search` with the draft profile and with the preset profile, on the same targets. This shows how much a hand-written profile helps.

### Task 11: eval-hidden on the new targets (paid, after my OK)

- First the 9 new targets once. I give the cost estimate before the run.

## Not in this plan

- The seller view for incense (demand, supplier, margin).
- Review sources for each category outside amazon.se.
- A profile that learns from many searches (source statistics, spec section 4.1).

## Progress (2026-10-17)

- Task 1 (4bb9e61). The title of a card joins its `h2` texts. The `h2` of a certificate ("TCO Certified", "Global Recycled Standard", "Energy Label") is not part of the title. 16 of 2184 cards in 60 saved fridge and headphones pages had such an `h2`.
- Task 2. `profiles/headphones.yaml` with 5 kinds and 7 attributes. I saved page 1 of the search words of each kind and of "trådlösa hörlurar" (6 pages, 281 cards, no model).
  - All 6 pages have the same filter groups. The useful word filters are form factor, noise control, connectivity, water resistance and colour. The only number groups are sensitivity and charge time, so no condition of the profile gets a number filter. The chat model chooses the word filters.
  - The title gives the battery life on 51 of the 281 cards ("50T Playtime", "45H Battery", "Up to 50 Hours of Battery Life"). I read all matches by hand and all were right. The units of hours ("h", "t", "hours", "timmar" and others) and grams are new in `title_values`.
  - Earbuds often give the time with the charging case. The label of the attribute says so.
- Task 3. `profiles/incense.yaml` with 6 kinds and 8 attributes, for a buyer. I saved page 1 of 7 searches (no model) and read the first 30 titles of each.
  - The search words of each kind give results of that kind: "rökelsepinnar", "rökelsekoner", "backflow rökelsekoner", "rökelseharts", "palo santo" and "rökelsehållare". Only "rökelseharts" mixes in charcoal tablets and a violin rosin. "rökelse" alone gives incense too, but its filters are the filters of heaters.
  - The filter groups are only brand, item form, product benefits and colour. No attribute gets a number filter, and the title check has no title words in this profile. The worker and the chat model do the work here.
  - The titles give the weight or the count ("40g (70-80 sticks)", "320 Sticks"). The chat model can calculate the price for each gram or each stick from them.
- Task 4. `profiles/running_shoes.yaml` with 2 kinds (road, trail) and 6 attributes. The size is a hard number attribute with `variant: size_name`.
  - "löparskor barn" gave shoes for women, and "trailskor" mixes in hiking shoes. Thus there is no kind for children. The Gender filter of the shop (Boys, Girls) does this work.
  - A product page of amazon.se has the variants in its script data (`dimensionValuesDisplayData`): the product id of each size and colour. The parser keeps them in `ProductRecord.options`. On 3 saved shoe pages it read 169, 79 and 47 variants. The sizes of the colour of the page were the same as the size buttons that the page shows as available. The other buttons lead to other colours.
  - The size attribute is the list of sizes, in the colour of the page first. A size that only another colour has gets that colour in its name ("40 EU (only in Pink)"). A condition passes when one size is inside it.
  - Before the picks, Python opens the page of the size of the shopper and checks its price and stock. It stores them as a variant of the listing. The card of the listing keeps the price and the stock of its own page, so the size of one shopper does not change the cards for the next one. The shown pick takes the price, the colour and size, and the link of the page of that size.
- Task 5. `update_needs` with a category that has no profile drafts one (one call of the chat model), saves it to `data/profiles/` and records the needs in the new category. The result tells the chat model that the category is new and not checked by a person. The context marks a draft category with "(draft)".
  - The draft prompt asks for each size as its own number, a label as a shop filter names it, and title words for numbers. `check_draft` rejects a hard attribute that is not a number, because only numbers can be conditions. The draft of a dishwasher from 2026-10 had "dimensions" as one hard text.
  - A change of decision 4: there is no separate match step after the first search. Python already matches a number filter group by the label of the attribute ("Height", "Capacity"), so the draft prompt asks for such labels. The model cannot know the filter names or the variant names of a shop before a search, so Python removes `site_filter` and `variant` from a draft.
- Task 6. The tool `update_profile` adds attributes, removes them, or makes them hard or soft. Python checks the names, the units, that only a number is hard, and that a removed attribute is not a condition of the needs. The changed profile is saved in `data/profiles/` with `edited: true`, and a changed copy of a preset comes before the preset file. The error of `update_needs` for an attribute that the profile does not have now names the tool.
- Task 7, the test of decision 5 (2026-10-17, no model cost). PaddleOCR-VL is in transformers 5.18, so no code from the model repository runs. The model is 1.8 GB. On the RTX 3060 it loaded in 72 s and used at most 2.2 GB, so it fits next to bge-m3. I gave it 3 images of the Candy CCG1S 518EW with the task "OCR:".
  - The energy label (537 x 1102): 24.5 s. It read the brand and the class letters, but not 203 kWh/annum, 64 l, 188 l or 41 dB.
  - The size drawing (801 x 1500): 57.1 s. It made a table "Candy Size 179.4cm, Candy Diameter 54.5cm". The numbers are right, but "Size" and "Diameter" are not in the image, and it gave 54.5 cm once for two sizes.
  - A photo without text: 4.5 s, the text "0".
  - Thus it runs, but it is slow and it misses or changes the facts that matter. The module (`extract/images.py`) and the storage of image text as chunks are built and tested. The agent does not use them yet. I decide how to continue.

### Code review of tasks 1 to 7 (2026-10-17)

The review found 10 problems. All are fixed.

- A size page that check_now stored became the price and the stock of the listing for all later searches. Now a card uses the variant of its own page, and only the shown pick uses the page of the size of the needs.
- `update_profile` could save two attributes with the same name, and then the profile did not load in any later turn. The changed profile now goes through the checks of `CategoryProfile` before it is saved. Drafts and `update_profile` share one attribute check (`profiles.attribute_errors`), so `update_profile` also refuses `price`, `rating` and `review_count`.
- A draft with the name of a preset hid the preset. Now only a copy marked `edited` comes before the preset.
- `update_needs` drafted a profile for a typo or a near name ("fridges"). Now it asks "Did you mean fridge?". A draft keeps the name that the chat model asked for, so the next call finds it.
- The image module lost all texts when one image did not open, could cut a thin last strip, and followed redirects to other hosts. All three are fixed.
- The parser took the first `"dimensions"` list of the page. It now takes the nearest list of the same length as the variants.
- Sizes with a fraction (38 2/3, kept as 38.67) now meet a condition of 38.6667.
- `save_record` and `save_variant` share one variant upsert.

### Images stopped (2026-10-17)

I decided to stop the image work for now: it is not necessary enough. Tasks 7 and 8 stop here. The module `extract/images.py` and the storage of image text stay, tested but not used. `read_images` still sends the images to the worker when the chat model asks.

- Task 9. 9 new targets in `eval/hidden/targets.yaml`, 3 in each new category: over-ear headphones with noise cancelling, sport headphones with solar charging, headphones for a child with a volume limit; Japanese sandalwood sticks, frankincense resin from Oman, a set of backflow cones; men's trail shoes in size 44, road shoes in size 42, black women's road shoes in size 38. I wrote the openings and the facts from the stored pages of 2026-10-17. All 9 were in stock and under their budgets.

### eval-search on the new targets (2026-10-17, no model)

First run, 9 new targets: before 7 of 9, without filters 5 of 9, with filters 4 of 9 (in the first 8 product pages of one search_shop call). The word filters dropped products that the shop tags wrongly or not at all: the over-ear headphones with noise cancelling are tagged "On Ear" (place 148), the backflow cones have no item form (place 154). With word filters, such products were only in the unfiltered list at the end.

Change: with word filters, the default order with only the price and number filters is a list of the first tier too (`crawl.result_lists`). One more search page for each call.

Second run, all 21 targets (small-freezer-or-fridge was out of stock, so 20 count):

| | before | without filters | with filters |
|---|---|---|---|
| 11 fridges | 5 | 7 | 7 |
| 9 new targets | 6 | 6 | 5 |
| all 20 | 11 | 13 | 12 |

- The change helped the two worst cases (148 to 51, 154 to 37), but they are still out of the first 8.
- The number and price filters help the fridges (narrow-cheap 39 to 6, dark-no-frost 13 to 2). The word filters do not help the new categories.
- retro-red was in none of the three searches this time; amazon changed its results again. The noise is large, and 20 targets is a small sample.
