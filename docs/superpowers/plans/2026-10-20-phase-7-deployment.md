# Phase 7: Deployment

The goal is one command that starts the whole app: the web page, the database and the web search. It runs on my computer, because the amazon.se window must be where I can log in and solve captchas.

## Design (2026-10-20)

- The app gets its own image (`Dockerfile`): Python 3.10, the package with the `retrieval` extra, and torch for the CPU only. bge-m3 and the reranker run in the container without a GPU. The image has the code, `config/` and `profiles/`. It has no keys, no browser profile, no pages and no labels (`.dockerignore`).
- `docker-compose.yml` has a new service `app` with the profile `app`. So `docker compose up -d` still starts only the database and SearXNG, as before, and `docker compose --profile app up -d --build` starts all three.
- The keys come from `.env` at run time (`env_file`). The app reaches the database as `db:5432` and SearXNG as `searxng:8080`.
- `data/` and `snapshots/` are mounted from the computer, so the stored pages stay out of the image and out of Git. The Hugging Face cache of the computer is mounted too, so the models are not downloaded again (`HF_CACHE`, by default `~/.cache/huggingface`).
- The page is published only to `127.0.0.1:9000`. `scout web --host 0.0.0.0` listens inside the container.
- Chrome stays on the computer. `scripts/start-browser.ps1` opens the product-scout profile with the DevTools port 9222. Chrome listens only on 127.0.0.1. The app connects to it over the Chrome DevTools Protocol (`BROWSER_CDP_URL`, Playwright `connect_over_cdp`) at `host.docker.internal`. It opens its own tab and closes only that tab, so the window and the login stay.
- Chrome refuses a DevTools request whose Host header is a name other than localhost. So the app uses the IP of `host.docker.internal` (`browser.cdp_endpoint`).
- Without `BROWSER_CDP_URL`, everything works as before: `scout web` on the computer opens its own Chrome window.

## Risks

- The DevTools port gives full control of the product-scout profile, with its amazon.se login. It listens only on 127.0.0.1, and only this profile has it, never my everyday profile.
- The CPU is slower than the RTX 3060 for bge-m3. A query is short, but the embedding of many new reviews can take a minute.
- If Docker Desktop does not pass `host.docker.internal` to 127.0.0.1 of the computer, the app cannot reach Chrome. I check this in the first real start.

## Tasks

1. The CDP option in `BrowserSession`, `BROWSER_CDP_URL` in the settings, `--host` for `scout web`. Tests with fakes. (done, 2026-10-20)
2. `Dockerfile`, `.dockerignore`, the `app` service, `scripts/start-browser.ps1`. (done, 2026-10-20)
3. Build the image and start it. Check: the page opens, a turn reaches the database, SearXNG answers, and a search opens a tab in the Chrome window. It needed downloads (the Python image, torch for the CPU and the packages). Done on 2026-10-20 after my OK.
   - The image is 1.72 GB.
   - Docker Desktop passes `host.docker.internal` (192.168.65.254) to 127.0.0.1 of the computer. The app read the DevTools version of Chrome, opened its own tab, loaded an amazon.se search page (status 200) and closed the tab. The window stayed open.
   - The page answered on 127.0.0.1:9000. The history and the memory routes read the database. SearXNG gave 20 results for one search.
   - bge-m3 loaded from the mounted cache without the network (`HF_HUB_OFFLINE=1`) in 63 s on the CPU, the first time in the container.
   - Not checked yet: a full turn with the chat model. It is part of the evaluations at the end.

## Not in this plan

- A public deployment, accounts, HTTPS. The page has no login, so it stays on localhost.
- The GPU in the container.
- Chrome inside the container.
