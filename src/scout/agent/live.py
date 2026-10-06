"""The real dependencies of the chat graph: Postgres, the amazon.se browser and the LLM."""
from scout.agent.graph import Deps
from scout.agent.lookup import image_reader
from scout.crawl import crawl as run_crawl
from scout.db.store import (add_eliminations, attribute_searches, eliminated_products, listing_source_ids,
                            mark_attribute_search, page_texts, restore_products, save_attribute)
from scout.extract.ondemand import KINDS, Store, fill_missing
from scout.extract.page import enrich_record
from scout.extract.screen import screen_titles
from scout.picks.pipeline import make_picks, refetcher, shortlist
from scout.picks.rank import load_cards_by_ids
from scout.profiles import load_profile, profile_names, save_draft
from scout.reviews import read_reviews
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
                **rest) -> Deps:
    """The parts of the dependencies that scout chat and scout eval-chat share, so both run the same picks.

    `refetch(profile)` gives the function that rechecks one card. `rest` holds the elimination store, say and tracer.
    Without `find_missing`, the picks do not look for missing condition values in the stored text.
    """
    screened: dict[tuple[str, int], bool] = {}  # a title is screened one time in a session
    search_text = hybrid_search(conn, BgeM3Embedder)  # one instance, so bge-m3 loads one time
    store = Store(
        retrieve=lambda listing_id, query, n: search_text(query, Scope(listing_ids=[listing_id], kinds=KINDS), n),
        save=lambda product_id, name, entry: save_attribute(conn, product_id, name, entry),
        searched=lambda ids: attribute_searches(conn, ids),
        mark=lambda product_id, name, found: mark_attribute_search(conn, product_id, name, found),
    )

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
