# Web chat page

I asked for this page on 2026-10-06, so I can test the agent myself. My wishes:

- The page is mainly a chat window.
- When it opens, one large message box is in the centre, as on chatgpt.com.
- The picks show in a panel at the right of the chat, like the panels of Claude's artifacts. The shopper can open the link of a pick or delete it.
- The design language is the one of the flowchart artifact: Source Sans 3 and JetBrains Mono, a cool grey background, white surfaces, thin borders, colour tokens with a dark mode.

The page runs on the local computer first (127.0.0.1). A public deployment needs its own plan, because the agent opens a Chrome profile on this computer and needs a login on amazon.se.

## Design

**Server.** FastAPI and uvicorn, started by `scout web`. It binds to 127.0.0.1 only. One graph, one database connection, and one turn at a time, as in `scout chat`. A second message while a turn runs gets status 409.

**One turn.** The browser sends the message. The server runs the graph in a worker thread and streams events back as lines of JSON (NDJSON) on the same response:

| Event | When |
|---|---|
| `status` | The graph calls `say`, for example the crawl report |
| `problem` | The browser found a captcha or a login page. The page shows the URL and two buttons, Done and Skip. The worker thread waits until the shopper clicks one |
| `reply` | One new assistant message |
| `picks` | The picks after the turn, with the removed products |
| `error` | The turn failed. The thread gets the message, as in `scout chat` |
| `done` | The end of the turn |

The login prompt replaces the terminal prompt of `ask_user`. The program never types a password. The shopper logs in or solves the captcha in the product-scout Chrome window, then clicks Done.

**Delete a pick.** The delete button calls `DELETE /api/threads/{thread}/picks/{product_id}`. Python does what `remove_picks` does: it adds the product to the eliminations of the thread, moves the pick to `removed` in the state, and adds the message "Removed: title" to the thread. No LLM call. The panel numbers the cards 1, 2, 3 in the order of the state, so "pick 2" in the next message means the card with the number 2. The panel does not fill the empty place. The shopper can ask for more in the chat.

**Pick view.** For each pick the server sends the title, the amazon.se URL, the price, the rating with the review count, at most 4 attributes, the first product image from `page_texts`, the doubtful sizes and the note. All values come from the cards in the database, as in the picks text.

**Threads.** The thread ID is in the URL (`#t=ab12cd34`), so a reload continues the conversation. The page asks `GET /api/threads/{thread}` for the messages and the picks. "New chat" makes a new ID. `scout chat --thread` and the page share the same threads.

**Front end.** One HTML file, one CSS file and one JavaScript file, served by FastAPI. No build step and no framework. The replies are plain text with line breaks. The page makes links of URLs and keeps the indents of the picks text.

| State | Layout |
|---|---|
| Empty thread | A heading and the large message box in the centre, with 3 example requests |
| Chat, no picks | Messages in one column of about 760 px, the message box at the bottom |
| Chat with picks | Chat at the left, the picks panel at the right (about 400 px). A button closes and opens the panel |
| Narrow screen (under 900 px) | The panel covers the chat. A button with the number of picks opens it |

## Tasks

1. **Plan.** This file.
2. **Service.** `src/scout/web/service.py`: `ChatService` runs a turn and sends the events, answers a login problem, removes a pick, and gives the history and the pick views. Tests with the fake world of `tests/test_graph.py`.
3. **App and command.** `src/scout/web/app.py` with the routes, and `scout web --port 9000`. Tests with the FastAPI test client.
4. **Page.** `src/scout/web/static/` with `index.html`, `app.css` and `app.js`. I check it in Chrome with a fake service that needs no LLM and no browser: the empty state, a turn, the picks panel, delete, the login banner, dark mode and a narrow window.
Tasks 1 to 4 are done (2026-10-06, 275 tests). All turns run in one worker thread, because Playwright works only in the thread that opened the browser. The Chrome extension could not open 127.0.0.1 on this computer, so I checked the page with headless Chrome through Playwright, on a fake world with real cards and images from the database. The checks passed: the empty state, a question, the login banner and Done, the picks panel, delete, a reload of the thread, dark mode, and a window 400 px wide. The browser console had no errors.

5. **My test.** I run `scout web` and talks with the agent. This is a real run with the paid chat model, a few cents a conversation.

6. **Progress of a turn (2026-10-13).** A turn takes about 10 minutes, so the page shows each step while it works. The agent loop sends a `step` event when a tool call starts and ends: the kind (needs, shop, web, db, reviews, check), a short title, the first line of the tool report, the seconds, and the calls, product pages and web searches used of the turn limits. The status lines of a tool (the search pages, the login) show under its step. A planning step of the model shows only while the model thinks. When the turn ends, the list closes to one line ("Worked for 10 min 32 s, 19 steps"), and a click opens it again. The steps are not stored, so a reload shows only the messages. The default port is now 9000: Windows keeps the ports 7276 to 8818 on this computer, so 8000 cannot open. I checked the page in Chrome with a demo server that sends scripted events.

7. **Fixed texts in the language of the shopper (2026-10-20).** Python and the page show their own texts in English, Swedish or Chinese. These are the buttons and hints of the page, the step titles, the status lines of the tools, the problem banner, and the messages that Python writes into the chat (errors, the turn limits, the gate that gives up, a deleted pick, and the labels of the pick lines).
   - `src/scout/lang.py` keeps the texts of Python. `src/scout/web/static/lang.js` keeps the texts of the page.
   - The start of each turn finds the language of the last message of the shopper and keeps it in the state (`lang`). Chinese characters give Chinese. Otherwise, common short words that are only Swedish or only English decide, and å, ä or ö count for Swedish. A message without a clear sign ("ok", "180 cm") keeps the language of the turn before. Other languages get English.
   - The page starts in the language of the browser. Each turn sends a `lang` event, and the history of a thread gives its language, so the page changes with the shopper.
   - What stays in English: the first line of each tool report under a step (it is written for the chat model), the names of the attributes on the pick cards, the details of the server errors on a page in another language (the page shows its own text), and the old fixed pipeline (`agent/graph.py`).
   - Checked with a fake agent loop in headless Chrome: a Chinese message turned the page, the step titles and the pick lines into Chinese, a Swedish browser showed the Swedish page, and the console had no errors.

## Not in this plan

- A list of old threads in a sidebar.
- Streaming of the reply word by word. The chat node checks each number before the text shows, so the text can only show when it is complete.
- More languages than English, Swedish and Chinese.
- Accounts, more than one shopper at a time, and a public deployment.
- Buttons for "show more" and "bring back". The shopper writes these in the chat.

## The list of the chats (2026-10-21)

- A list of the conversations is on the left of the page, newest first, at most 30. The title of a conversation is the start of its first message (80 characters). The date is the time of its last checkpoint. The current conversation is marked. A click opens it.
- On a wide screen the list is open, unless I closed it (the browser keeps this choice). On a screen of 900 px or less it opens over the chat from the menu button, and it closes when I open a conversation or the picks.
- The route `GET /api/threads` reads the newest threads from the checkpoints table (`db.store.recent_threads`, also used by `scout threads`). The titles are kept in memory, because a first message does not change.
- A short read of the page (the history, the list, the memory) now waits up to 2 seconds for another short read. Before, two reads at the same time gave "a turn is running".
- The hidden picks panel no longer makes the page scroll to the side.
