"""The real dependencies of the chat graph: Postgres, the amazon.se browser and the LLM."""
from scout.agent.graph import Deps
from scout.agent.lookup import image_reader
from scout.crawl import crawl as run_crawl
from scout.db.store import add_eliminations, eliminated_products, page_texts, restore_products
from scout.extract.page import enrich_record
from scout.extract.screen import screen_titles
from scout.picks.pipeline import make_picks, refetcher, shortlist
from scout.picks.rank import load_cards_by_ids
from scout.profiles import load_profile, profile_names, save_draft
from scout.sources.amazon_se.adapter import AmazonSeAdapter
from scout.sources.browser import BrowserSession
from scout.text.embed import BgeM3Embedder
from scout.text.retrieve import hybrid_search


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


def shared_deps(conn, llm, crawl, refetch, k: int = 5, size_check: bool = True, **rest) -> Deps:
    """The parts of the dependencies that scout chat and scout eval-chat share, so both run the same picks.

    `refetch(profile)` gives the function that rechecks one card. `rest` holds the elimination store, say and tracer.
    """
    screened: dict[tuple[str, int], bool] = {}  # a title is screened one time in a session

    def count(profile, needs, excluded):
        return len(shortlist(conn, profile, needs, llm, excluded, screened).ranked)

    def picks(profile, needs, excluded, kept, first, language="English"):
        short = shortlist(conn, profile, needs, llm, set(excluded) | {c.product_id for c in [*kept, *first]}, screened)
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
        search_text=hybrid_search(conn, BgeM3Embedder),
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

    return shared_deps(
        conn, llm, crawl, refetch, k,
        eliminate=lambda thread, ids, reason: add_eliminations(conn, thread, ids, reason),
        eliminated=lambda thread: eliminated_products(conn, thread),
        restore=lambda thread, ids: restore_products(conn, thread, ids),
        say=say,
        tracer=tracer,
    )
