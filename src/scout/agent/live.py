"""The real dependencies of the chat graph: Postgres, the amazon.se browser and the LLM."""
import threading

from scout.agent.graph import Deps
from scout.agent.lookup import image_reader
from scout.crawl import CrawlReport, read_details
from scout.crawl import crawl as run_crawl
from scout.db.store import (add_eliminations, attribute_searches, complete_review_reads, eliminated_products,
                            known_source_ids, listing_source_ids, refresh_listings,
                            mark_attribute_search, page_texts, product_listing_ids, restore_products,
                            save_attribute)
from scout.extract.ondemand import KINDS, Store, fill_missing
from scout.extract.page import enrich_record
from scout.extract.variants import size_page
from scout.extract.screen import screen_titles
from scout.picks.pipeline import make_picks, refetcher, shortlist, variant_refetcher
from scout.picks.opinions import pick_opinions
from scout.picks.rank import load_cards_by_ids
from scout.profiles import load_profile, profile_names, save_draft
from scout.lang import t
from scout.reviews import FRESH, read_reviews
from scout.sources.amazon_se.adapter import AmazonSeAdapter
from scout.sources.amazon_se.urls import SOURCE, asin_from_url
from scout.sources.browser import BrowserSession
from scout.text.embed import BgeM3Embedder
from scout.text.retrieve import Scope, hybrid_search


class LazyBrowser:
    """Opens the product-scout Chrome profile only when a step needs amazon.se."""

    def __init__(self, settings):
        self._settings, self._session, self._adapter = settings, None, None

    def adapter(self) -> AmazonSeAdapter:
        if self._adapter is None:
            self._session = BrowserSession(self._settings.browser_profile_dir,
                                           cdp_url=self._settings.browser_cdp_url).__enter__()
            self._adapter = AmazonSeAdapter(self._session, self._settings.snapshot_dir)
        return self._adapter

    def fetch(self, url: str):
        """A page of another shop in the same Chrome profile, when a plain request is refused (scout.fetch)."""
        self.adapter()
        return self._session.fetch(url)

    def close(self) -> None:
        if self._session is not None:
            self._session.__exit__(None, None, None)
            self._session, self._adapter = None, None


SHOPPER = "me"  # one shopper on this computer; the web page has no login
LIST_PAGES = 2  # result pages of the default order that the first search_shop call reads


def shared_deps(conn, llm, crawl, refetch, k: int = 5, size_check: bool = True, find_missing: bool = True,
                write_opinions: bool = True, **rest) -> Deps:
    """The parts of the dependencies of the picks pipeline (scout chat --pipeline).

    `refetch(profile)` gives the function that rechecks one card. `rest` holds the elimination store, say and tracer.
    Without `find_missing`, the picks do not look for missing condition values in the stored text. Without
    `write_opinions`, no pros and complaints are written after the picks.
    """
    screened: dict[tuple[str, int], bool] = {}  # a title is screened one time in a session
    search_text = hybrid_search(conn, BgeM3Embedder)  # one instance, so bge-m3 loads one time
    store = Store(
        retrieve=lambda product_id, query, n: search_text(
            query, Scope(listing_ids=product_listing_ids(conn, product_id), kinds=KINDS), n),
        save=lambda product_id, name, entry: save_attribute(conn, product_id, name, entry),
        searched=lambda ids: attribute_searches(conn, ids),
        mark=lambda product_id, name, found: mark_attribute_search(conn, product_id, name, found),
    )

    def opinions(profile, picks, language):
        cards = {c.listing_id: c for c in load_cards_by_ids(conn, [listing_id for _, listing_id in picks])}
        indexed: list[bool] = []

        def search_for(card):
            scope = Scope(listing_ids=product_listing_ids(conn, card.product_id), kinds=["review"])

            def search(query, n):
                hits = search_text(query, scope, n, index=not indexed)  # the chunks are indexed one time
                indexed.append(True)
                return hits
            return search
        numbered = [(n, cards[listing_id]) for n, listing_id in picks if listing_id in cards]
        return pick_opinions(llm, numbered, profile.complaint_types, search_for, language)

    def count(profile, needs, excluded):
        return len(shortlist(conn, profile, needs, llm, excluded, screened).ranked)

    def picks(profile, needs, excluded, kept, first, language="English"):
        fill = None
        if find_missing and llm is not None:
            def fill(cards, conditions):
                return fill_missing(cards, conditions, profile, llm, store)
        short = shortlist(conn, profile, needs, llm, set(excluded) | {c.product_id for c in [*kept, *first]}, screened,
                          fill)
        return make_picks(short, needs, k, refetch(profile), llm, language, kept=kept, first=first,
                          size_check=size_check)

    return Deps(
        llm=llm,
        profile_names=profile_names,
        save_draft=save_draft,
        load_profile=load_profile,
        count=count,
        crawl=crawl,
        picks=picks,
        cards=lambda ids: load_cards_by_ids(conn, ids),
        page_texts=lambda ids: page_texts(conn, ids),
        query=lambda profile, needs: shortlist(conn, profile, needs, llm, (), screened).ranked,
        read_images=image_reader(llm),
        search_text=search_text,
        opinions=opinions if write_opinions and llm is not None else None,
        **rest,
    )


def live_deps(conn, llm, browser: LazyBrowser, on_problem, say, tracer, k: int = 5) -> Deps:
    def crawl(profile, needs, page):
        return run_crawl(conn, browser.adapter(), profile.category, needs.search_terms(profile),
                         on_problem=on_problem, first_page=page, max_pages=1, max_details=20,
                         budget=needs.budget_sek(), skip_known=True,
                         enrich=lambda rec: enrich_record(rec, profile, llm),
                         screen=lambda cands: screen_titles(cands, profile, llm, needs.wanted(profile)))

    def refetch(profile):
        def one(card):  # the browser opens only when there is a card to check
            return refetcher(browser.adapter(), conn, profile.category, on_problem)(card)
        return one

    def reviews(picks):
        ids = [p["listing_id"] for p in picks]
        asins = listing_source_ids(conn, SOURCE, ids)
        targets = [(i, asins[i]) for i in ids if i in asins]
        return read_reviews(conn, browser.adapter, targets, on_problem, say=say).text()

    return shared_deps(
        conn, llm, crawl, refetch, k,
        read_reviews=reviews,
        eliminate=lambda thread, ids, reason: add_eliminations(conn, thread, ids, reason),
        eliminated=lambda thread: eliminated_products(conn, thread),
        restore=lambda thread, ids: restore_products(conn, thread, ids),
        say=say,
        tracer=tracer,
    )


def web_client():
    """The web search back ends (decided 2026-10-07): our own SearXNG first (SEARXNG_URL, by default the Docker
    service on this computer), then Tavily when TAVILY_API_KEY is set. WEB_SEARCH=tavily or WEB_SEARCH=searxng uses
    only that one, WEB_SEARCH=off none."""
    import os

    from scout.websearch import FirstThatAnswers, SearxngClient, TavilyClient
    choice = os.environ.get("WEB_SEARCH", "").strip().lower()
    if choice == "off":
        return None
    if choice not in ("", "searxng", "tavily"):
        raise ValueError(f"WEB_SEARCH is '{choice}'. Use searxng, tavily or off, or leave it empty for both")
    if choice == "tavily" and not os.environ.get("TAVILY_API_KEY"):
        raise ValueError("WEB_SEARCH is tavily, but TAVILY_API_KEY is not set")
    clients = []
    if choice in ("", "searxng"):
        clients.append(SearxngClient(os.environ.get("SEARXNG_URL") or "http://127.0.0.1:8888"))
    key = os.environ.get("TAVILY_API_KEY")
    if key and choice in ("", "tavily"):
        clients.append(TavilyClient(key))
    if not clients:
        return None
    return clients[0] if len(clients) == 1 else FirstThatAnswers(clients)


def loop_deps(conn, llm, browser: LazyBrowser, on_problem, say, tracer, make_embedder=BgeM3Embedder,
              web="env", shopper: str = SHOPPER, search_log: list | None = None):
    """The dependencies of the agent loop (agent/loop.py): the same stores, browser and checks as the pipeline.
    make_embedder: a run with many conversations gives one that returns the same embedder, so bge-m3 loads once.
    web: a search client, None for no web search, or "env" for the back ends of web_client().
    shopper: whose memories the agent reads and keeps. An evaluation gives each target its own.
    search_log: when given, each shop search adds what happened to each of its results (CrawlReport.outcomes)."""
    from datetime import datetime, timedelta, timezone

    from scout.db.store import add_memory, forget_memories, memories
    from scout.db.store import save_web_chunks, save_web_search, stored_web_results, web_searched
    from scout.websearch import ProductRef, WebResult, WebStore, fetch_texts, url_ref
    from scout.review_sources import not_for_tests
    from scout.websearch import research as run_research
    from scout.websearch import search_web as run_web_search

    from scout.agent.graph import crawl_text
    from scout.agent.loop import LoopDeps
    from scout.fetch import PageFetcher, site_of
    from scout.shops import read_page as read_shop_page
    from scout.shops import refetch as refetch_shop_page
    from scout.extract.ondemand import fill_missing as fill
    from scout.picks.rank import Condition
    from scout.picks.wishes import judge_wishes
    from scout.db.conn import ThreadConnections

    if not isinstance(conn, ThreadConnections):  # each candidate researcher gets its own connection
        conn = ThreadConnections(conn)

    screened: dict[tuple[str, int], bool] = {}
    search_text = hybrid_search(conn, make_embedder)
    store = Store(
        retrieve=lambda product_id, query, n: search_text(
            query, Scope(listing_ids=product_listing_ids(conn, product_id), kinds=KINDS), n),
        save=lambda product_id, name, entry: save_attribute(conn, product_id, name, entry),
        searched=lambda ids: attribute_searches(conn, ids),
        mark=lambda product_id, name, found: mark_attribute_search(conn, product_id, name, found),
    )

    # The kept results that a search did not read yet, by (category, words, page, budget, wanted): the next call
    # with the same words reads them without loading the search pages again
    unread: dict[tuple, list] = {}
    read_to: dict[tuple, int] = {}  # the last result page read of a search, by the key of unread without the page

    def search_shop(profile, needs, words, page, max_details):
        from scout.search.filters import off_condition
        adapter = browser.adapter()
        enrich = lambda rec: enrich_record(rec, profile, llm)  # noqa: E731
        budget = needs.budget_sek()
        key = (profile.category, words, page, budget, repr(needs.wanted(profile)), repr(needs.conditions))
        if unread.get(key):
            known = known_source_ids(conn, SOURCE, [c.source_id for c in unread[key]])
            refresh_listings(conn, [c for c in unread[key] if c.source_id in known])
            todo = [c for c in unread[key] if c.source_id not in known]
            report = read_details(conn, adapter, profile.category, todo[:max_details], CrawlReport(),
                                  on_problem=on_problem, enrich=enrich)
            unread[key] = todo[max_details:]
            report.outcomes.update({i: "stored_before" for i in known})
            report.outcomes.update({c.source_id: "unread" for c in unread[key]})
            text = (f"Read the next results of '{words}' (result page {page}) without loading the search pages "
                    f"again: {report.saved} new products saved.")
            text = "\n".join([text, *report.notes])
        else:
            # The first call can read more than one result page: a later page starts after the last one read
            search = key[:2] + key[3:]
            start = max(page, read_to.get(search, 0) + 1) if page > 1 else page
            report = run_crawl(conn, adapter, profile.category, words, on_problem=on_problem, first_page=start,
                               max_pages=LIST_PAGES if page == 1 else 1, max_details=max_details,
                               budget=budget, skip_known=True,
                               enrich=enrich, card_check=lambda c: off_condition(c, needs, profile),
                               screen=lambda cands: screen_titles(cands, profile, llm, needs.wanted(profile),
                                                                  needs.text() or None))
            unread[key] = report.unread
            read_to[search] = max(read_to.get(search, 0), report.last_page)
            text = crawl_text(report, words, start)
            if report.last_page > start:
                text += (f"\nResult pages {start} to {report.last_page} are read. For more, call search_shop with "
                         f"page {report.last_page + 1}.")
        if search_log is not None:
            search_log.append({"words": words, "page": page, "outcomes": dict(report.outcomes)})
        if report.skipped:
            text += f"\n{len(report.skipped)} product pages did not load: " + ", ".join(report.skipped)
        if unread[key]:
            text += (f"\n{len(unread[key])} more results fit and are not read yet. Call search_shop with the same "
                     "words and page to read the next ones.")
        return text, report.detail_pages

    fetcher = PageFetcher(browser=browser.fetch)

    def read_page(url, profile, use_browser=True):
        got = read_shop_page(conn, lambda u: fetcher.fetch(u, use_browser=use_browser), url, profile.category,
                             lambda rec: enrich_record(rec, profile, llm), lock=db_lock)
        return got.text, got.pages, got.listing_id

    def same_product(listing_id, same_as):
        from scout.db.store import join_products
        try:
            problem = join_products(conn, listing_id, same_as)
        except Exception:
            conn.rollback()
            raise
        conn.commit()
        return problem

    def check_now(ids, needs):
        problems = {}
        budget = needs.budget_sek()
        cards = load_cards_by_ids(conn, ids)
        rows = conn.execute("SELECT id, category FROM products WHERE id = ANY(%s)",
                            ([c.product_id for c in cards],)).fetchall()
        category = {row["id"]: row["category"] for row in rows}
        refetch = {}
        for card in cards:
            cat = category[card.product_id]
            other_shop = site_of(card.url) != "amazon.se"  # a pick from read_page
            # A condition on a variant (the shoe size): the page of that size has its own price and stock
            size = None if other_shop else size_page(card.attributes, needs, load_profile(cat))
            if size == ("", ""):
                problems[card.listing_id] = "the shop has no page for the size of the needs"
                continue
            if size and size[1] != asin_from_url(card.url):
                rec = variant_refetcher(browser.adapter(), conn, on_problem)(card.listing_id, size[1])
            elif other_shop:
                # The worker reads only a missing price or stock, not the attributes again
                core = load_profile(cat).model_copy(update={"attributes": []})
                rec = refetch_shop_page(conn, fetcher.fetch, card.url, cat, lambda r: enrich_record(r, core, llm))
            else:
                if cat not in refetch:
                    refetch[cat] = refetcher(browser.adapter(), conn, cat, on_problem)
                rec = refetch[cat](card)
            if rec is None:
                problems[card.listing_id] = "the page did not load now, so the price and the stock are not known"
            elif rec.in_stock is False:
                problems[card.listing_id] = f"size {size[0]} sold out now" if size else "sold out now"
            elif budget is not None and rec.price is None:
                problems[card.listing_id] = "shows no price now, so the budget cannot be checked"
            elif budget is not None and other_shop and rec.currency != "SEK":  # a shop in another country
                currency = rec.currency or "in a currency that the page does not name"
                problems[card.listing_id] = (f"costs {rec.price} {currency}, and the budget is in SEK, so Python "
                                             "cannot check it")
            elif budget is not None and rec.price > budget:
                problems[card.listing_id] = f"costs {rec.price} {rec.currency} now, over the budget"
            else:
                problems[card.listing_id] = ""
        return problems

    def find_missing(cards, attributes, profile):
        notes = fill(cards, [Condition(a, "<=", 0) for a in attributes], profile, llm, store, max_cards=len(cards))
        lines = [f"id {c.listing_id}: " + ", ".join(
            f"{a}={c.attributes[a]['value']} {c.attributes[a].get('unit') or ''} (raw '{c.attributes[a].get('raw')}')"
            if (c.attributes.get(a) or {}).get("value") is not None else f"{a} not found" for a in attributes)
            for c in cards]
        return "\n".join([*notes, *lines])

    def reviews(ids):
        asins = listing_source_ids(conn, SOURCE, ids)
        missing = [i for i in ids if i not in asins]
        report = read_reviews(conn, browser.adapter, [(i, asins[i]) for i in ids if i in asins], on_problem, say=say,
                              notice=t("say_reviews", deps.lang))
        text = report.text() + "".join(f" Id {i} is from another shop: read_page stored the reviews that its "
                                       "page shows." for i in missing)
        return text.strip(), report.pages, missing + ([i for i in ids if i in asins] if report.skipped else [])

    def reviews_read(ids):
        now = datetime.now(timezone.utc)
        return {i for i, at in complete_review_reads(conn, ids).items() if now - at < FRESH}

    def summarize(profile, ids, language):
        cards = load_cards_by_ids(conn, ids)

        def search_for(card):
            scope = Scope(listing_ids=product_listing_ids(conn, card.product_id), kinds=["review"])
            return lambda query, n: search_text(query, scope, n)
        text, _ = pick_opinions(llm, [(c.listing_id, c) for c in cards], profile.complaint_types, search_for,
                                language)
        return text or "these products have too few stored reviews to summarize."

    client = web_client() if web == "env" else web
    week = timedelta(days=7)

    def stored(question):
        # A search that found nothing is asked again: the engines of SearXNG can be busy for a short time
        rows = stored_web_results(conn, question, datetime.now(timezone.utc) - week)
        return [WebResult(**r) for r in rows] if rows else None

    db_lock = threading.RLock()  # the candidate researchers share this connection (agent/researcher.py)

    def locked(fn):
        def call(*args):
            with db_lock:
                return fn(*args)
        return call

    web_store = WebStore(
        stored=stored,
        save_search=lambda question, ids, results: save_web_search(
            conn, question, ids, [vars(r) for r in results], datetime.now(timezone.utc) - week),
        save_chunks=lambda listing_id, url, chunks: save_web_chunks(
            conn, listing_id, url_ref(url), chunks, datetime.now(timezone.utc)),
    )

    from scout.db.store import log_fetch
    from scout.reddit import reddit_reader

    reddit = reddit_reader(locked(lambda *row: log_fetch(conn, *row)))  # None without REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET

    def extract(urls):
        return fetch_texts(lambda u: fetcher.fetch(u, use_browser=False), urls, read=reddit)

    loaded_reranker: list = []
    rerank_lock = threading.Lock()  # the candidate researchers search the web at the same time

    def reranker(question, texts):  # loads at the first web search that has text to score
        if not texts:
            return []
        with rerank_lock:
            if not loaded_reranker:
                from scout.text.rerank import BgeReranker
                loaded_reranker.append(BgeReranker())
            return loaded_reranker[0](question, texts)

    def search_web(question, ids, max_pages, want=""):
        with db_lock:
            cards = load_cards_by_ids(conn, ids)
        products = [ProductRef(c.listing_id, c.title, c.model, c.brand) for c in cards]
        # Plain requests only: the texts load in worker threads, where the browser cannot run. The database parts
        # take the lock, so a researcher can search the web while another one reads the database.
        store = WebStore(locked(web_store.stored), locked(web_store.save_search), locked(web_store.save_chunks))
        report = run_web_search(question, products, client, llm, store, reranker, max_pages, want=want,
                                extract=extract)
        with db_lock:
            conn.commit()
        return report.text, report.pages, report.credits

    def research_web(items, max_pages, want=""):
        cards = {c.listing_id: c for c in load_cards_by_ids(conn, [i for i, _ in items])}
        todo = [(ProductRef(i, cards[i].title, cards[i].model, cards[i].brand), words) for i, words in items
                if i in cards]
        report = run_research(todo, client, llm, web_store, reranker, max_pages, want=want,
                              extract=extract, lock=db_lock)
        with db_lock:
            conn.commit()
        return report

    verdicts: dict = {}  # (wish, listing id) -> the worker's verdict, for the session
    deps = LoopDeps(
        llm=llm, profile_names=profile_names, load_profile=load_profile, save_draft=save_draft,
        cards=lambda ids: load_cards_by_ids(conn, ids),
        find=lambda profile, needs, excluded: shortlist(conn, profile, needs, llm, excluded, screened).ranked,
        eliminate=lambda thread, ids, reason: add_eliminations(conn, thread, ids, reason),
        eliminated=lambda thread: eliminated_products(conn, thread),
        restore=lambda thread, ids: restore_products(conn, thread, ids),
        search_shop=search_shop, check_now=check_now,
        page_texts=lambda ids: page_texts(conn, ids), search_text=search_text,
        similar_products=search_text.similar, read_images=image_reader(llm),
        judge_wishes=lambda cards, wishes: judge_wishes(llm, cards, wishes, search_text.wishes, verdicts),
        find_missing=find_missing, read_reviews=reviews, reviews_read=reviews_read, summarize=summarize,
        search_web=search_web if client is not None else None,
        research_web=research_web if client is not None else None, read_page=read_page, same_product=same_product,
        web_searched=lambda ids: web_searched(conn, ids, datetime.now(timezone.utc) - week, not_for_tests()),
        memories=lambda: memories(conn, shopper),
        remember=lambda kind, text, category, thread, source="remember": add_memory(
            conn, shopper, kind, text, category, thread, source),
        forget=lambda ids: forget_memories(conn, shopper, ids),
        say=say, tracer=tracer, db_lock=db_lock, db=conn,
    )
    return deps
