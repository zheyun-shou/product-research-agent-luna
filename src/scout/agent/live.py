"""The real dependencies of the chat graph: Postgres, the amazon.se browser and the LLM."""
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
from scout.extract.screen import screen_titles
from scout.picks.pipeline import make_picks, refetcher, shortlist
from scout.picks.opinions import pick_opinions
from scout.picks.rank import load_cards_by_ids
from scout.profiles import load_profile, profile_names, save_draft
from scout.reviews import FRESH, read_reviews
from scout.sources.amazon_se.adapter import AmazonSeAdapter
from scout.sources.amazon_se.urls import SOURCE
from scout.sources.browser import BrowserSession
from scout.text.embed import BgeM3Embedder
from scout.text.retrieve import Scope, hybrid_search


class LazyBrowser:
    """Opens the product-scout Chrome profile only when a step needs amazon.se."""

    def __init__(self, settings):
        self._settings, self._session, self._adapter = settings, None, None

    def adapter(self) -> AmazonSeAdapter:
        if self._adapter is None:
            self._session = BrowserSession(self._settings.browser_profile_dir).__enter__()
            self._adapter = AmazonSeAdapter(self._session, self._settings.snapshot_dir)
        return self._adapter

    def close(self) -> None:
        if self._session is not None:
            self._session.__exit__(None, None, None)
            self._session, self._adapter = None, None


def shared_deps(conn, llm, crawl, refetch, k: int = 5, size_check: bool = True, find_missing: bool = True,
                write_opinions: bool = True, **rest) -> Deps:
    """The parts of the dependencies that scout chat and scout eval-chat share, so both run the same picks.

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
    """The Tavily client when TAVILY_API_KEY is set, else None (then the loop has no search_web)."""
    import os

    from scout.websearch import TavilyClient
    key = os.environ.get("TAVILY_API_KEY")
    return TavilyClient(key) if key else None


def loop_deps(conn, llm, browser: LazyBrowser, on_problem, say, tracer, make_embedder=BgeM3Embedder,
              web="env"):
    """The dependencies of the agent loop (agent/loop.py): the same stores, browser and checks as the pipeline.
    make_embedder: a run with many conversations gives one that returns the same embedder, so bge-m3 loads once.
    web: a Tavily client, None for no web search, or "env" for the key in the environment."""
    from datetime import datetime, timedelta, timezone

    from scout.db.store import save_web_chunks, save_web_search, stored_web_results, web_searched, web_texts
    from scout.websearch import ProductRef, WebResult, WebStore, search_web as run_web_search, url_ref

    from scout.agent.graph import crawl_text
    from scout.agent.loop import LoopDeps
    from scout.extract.ondemand import fill_missing as fill
    from scout.picks.rank import Condition

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

    def search_shop(profile, needs, words, page, max_details):
        adapter = browser.adapter()
        enrich = lambda rec: enrich_record(rec, profile, llm)  # noqa: E731
        budget = needs.budget_sek()
        key = (profile.category, words, page, budget, repr(needs.wanted(profile)))
        if unread.get(key):
            known = known_source_ids(conn, SOURCE, [c.source_id for c in unread[key]])
            refresh_listings(conn, [c for c in unread[key] if c.source_id in known])
            todo = [c for c in unread[key] if c.source_id not in known]
            report = read_details(conn, adapter, profile.category, todo[:max_details], CrawlReport(),
                                  on_problem=on_problem, enrich=enrich)
            unread[key] = todo[max_details:]
            text = (f"Read the next results of '{words}' (result page {page}) without loading the search pages "
                    f"again: {report.saved} new products saved.")
            text = "\n".join([text, *report.notes])
        else:
            report = run_crawl(conn, adapter, profile.category, words, on_problem=on_problem, first_page=page,
                               max_pages=1, max_details=max_details, sorts=adapter.sorts if page == 1 else (),
                               budget=budget, skip_known=True, enrich=enrich,
                               screen=lambda cands: screen_titles(cands, profile, llm, needs.wanted(profile)))
            unread[key] = report.unread
            text = crawl_text(report, words, page)
        if report.skipped:
            text += f"\n{len(report.skipped)} product pages did not load: " + ", ".join(report.skipped)
        if unread[key]:
            text += (f"\n{len(unread[key])} more results fit and are not read yet. Call search_shop with the same "
                     "words and page to read the next ones.")
        return text, report.detail_pages

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
            if cat not in refetch:
                refetch[cat] = refetcher(browser.adapter(), conn, cat, on_problem)
            rec = refetch[cat](card)
            if rec is None:
                problems[card.listing_id] = "the page did not load now, so the price and the stock are not known"
            elif rec.in_stock is False:
                problems[card.listing_id] = "sold out now"
            elif budget is not None and rec.price is None:
                problems[card.listing_id] = "shows no price now, so the budget cannot be checked"
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
        report = read_reviews(conn, browser.adapter, [(i, asins[i]) for i in ids if i in asins], on_problem, say=say)
        text = report.text() + "".join(f" Id {i} is not an amazon.se product, so its reviews cannot be read."
                                       for i in missing)
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
        rows = stored_web_results(conn, question, datetime.now(timezone.utc) - week)
        return None if rows is None else [WebResult(**r) for r in rows]

    web_store = WebStore(
        stored=stored,
        save_search=lambda question, ids, results: save_web_search(
            conn, question, ids, [vars(r) for r in results]),
        save_chunks=lambda listing_id, url, chunks: save_web_chunks(
            conn, listing_id, url_ref(url), chunks, datetime.now(timezone.utc)),
    )

    def search_web(question, ids, max_pages):
        cards = load_cards_by_ids(conn, ids)
        products = [ProductRef(c.listing_id, c.title, c.model) for c in cards]
        report = run_web_search(question, products, client, llm, web_store, max_pages)
        conn.commit()
        return report.text, report.pages

    return LoopDeps(
        llm=llm, profile_names=profile_names, load_profile=load_profile,
        cards=lambda ids: load_cards_by_ids(conn, ids),
        find=lambda profile, needs, excluded: shortlist(conn, profile, needs, llm, excluded, screened).ranked,
        eliminate=lambda thread, ids, reason: add_eliminations(conn, thread, ids, reason),
        eliminated=lambda thread: eliminated_products(conn, thread),
        restore=lambda thread, ids: restore_products(conn, thread, ids),
        search_shop=search_shop, check_now=check_now,
        page_texts=lambda ids: page_texts(conn, ids), search_text=search_text, read_images=image_reader(llm),
        find_missing=find_missing, read_reviews=reviews, reviews_read=reviews_read, summarize=summarize,
        search_web=search_web if client is not None else None,
        web_searched=lambda ids: web_searched(conn, ids, datetime.now(timezone.utc) - week),
        web_texts=lambda ids: web_texts(conn, ids),
        say=say, tracer=tracer,
    )
