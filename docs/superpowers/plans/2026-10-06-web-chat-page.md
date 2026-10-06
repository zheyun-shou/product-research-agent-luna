# Web chat page

Zheyun asked for this page on 2026-10-06, so she can test the agent herself. Her wishes:

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
3. **App and command.** `src/scout/web/app.py` with the routes, and `scout web --port 8000`. Tests with the FastAPI test client.
4. **Page.** `src/scout/web/static/` with `index.html`, `app.css` and `app.js`. Claude checks it in Chrome with a fake service that needs no LLM and no browser: the empty state, a turn, the picks panel, delete, the login banner, dark mode and a narrow window.
5. **Her test.** Zheyun runs `scout web` and talks with the agent. This is a real run with the paid chat model, a few cents a conversation.

## Not in this plan

- A list of old threads in a sidebar.
- Streaming of the reply word by word. The chat node checks each number before the text shows, so the text can only show when it is complete.
- Accounts, more than one shopper at a time, and a public deployment.
- Buttons for "show more" and "bring back". The shopper writes these in the chat.
