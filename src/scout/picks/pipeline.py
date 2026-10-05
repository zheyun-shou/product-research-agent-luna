"""The picks steps, shared by `scout picks` and the chat agent."""
from dataclasses import dataclass, field
from typing import Callable, Collection, Sequence

import psycopg

from scout.crawl import OnProblem, fetch_checked
from scout.db.store import save_record
from scout.extract.doubt import check_sizes
from scout.extract.screen import screen_titles
from scout.llm.client import LLMOutputError, LLMUnavailable
from scout.needs import Needs
from scout.picks.rank import Card, apply_conditions, drop_implausible_sizes, load_cards, rank
from scout.picks.recheck import recheck_picks
from scout.picks.write import PicksText, format_picks, write_picks
from scout.profiles import CategoryProfile
from scout.sources.base import ProductRecord

Refetch = Callable[[Card], ProductRecord | None]


@dataclass
class Shortlist:
    ranked: list[Card]
    dropped: int
    notes: list[str] = field(default_factory=list)
    size_notes: dict[int, str] = field(default_factory=dict)


@dataclass
class PicksResult:
    chosen: list[Card]
    texts: PicksText | None
    errors: list[str]
    notes: list[str]
    dropped: int
    conditions_text: str
    card_notes: dict[int, str] = field(default_factory=dict)  # listing_id -> the size notes of that pick

    def render(self) -> str:
        return format_picks(self.chosen, self.texts, self.notes, self.conditions_text, self.dropped, self.errors)


def shortlist(conn: psycopg.Connection, profile: CategoryProfile, needs: Needs, llm,
              excluded: Collection[int] = (), screened: dict[tuple[str, int], bool] | None = None) -> Shortlist:
    """SQL filter, implausible sizes, the user's conditions, the rank, and the title screening.

    `screened` keeps the screening results (wanted kind, listing_id) -> keep, so a title is screened one time.
    """
    cards = [c for c in load_cards(conn, profile.category, needs.budget_sek()) if c.product_id not in excluded]
    size_notes = drop_implausible_sizes(cards)
    cards, dropped = apply_conditions(cards, needs.rank_conditions())
    ranked = rank(cards)
    notes: list[str] = []
    if llm is None:
        notes.append("Titles were not screened, so items outside the category can appear.")
    elif ranked:
        wanted = needs.wanted(profile)
        screened = {} if screened is None else screened
        todo = [c for c in ranked if (wanted, c.listing_id) not in screened]
        try:
            if todo:
                kept = {c.listing_id for c in screen_titles(todo, profile, llm, wanted)}
                screened.update({(wanted, c.listing_id): c.listing_id in kept for c in todo})
            on_topic = [c for c in ranked if screened[(wanted, c.listing_id)]]
            if len(on_topic) < len(ranked):
                notes.append(f"{len(ranked) - len(on_topic)} stored items are not a "
                             f"'{wanted}' by their titles and were left out.")
            ranked = on_topic
        except (LLMUnavailable, LLMOutputError) as exc:
            notes.append(f"Title screening skipped: {exc}")
    return Shortlist(ranked, dropped, notes, size_notes)


def make_picks(short: Shortlist, needs: Needs, k: int, refetch: Refetch, llm, language: str = "English",
               kept: Sequence[Card] = (), first: Sequence[Card] = ()) -> PicksResult:
    """Recheck the top cards on the live pages, check their sizes, then write summaries with checked numbers.

    `kept` cards stay picks without a new check. `first` cards are checked before the shortlist. When kept and
    first are more than k, the last kept cards go.
    """
    kept = list(kept)[:max(0, k - len(first))]
    fetched: dict[int, ProductRecord] = {}

    def refetch_and_keep(card: Card) -> ProductRecord | None:
        rec = refetch(card)
        if rec is not None:
            fetched[card.listing_id] = rec
        return rec

    taken = {c.product_id for c in [*kept, *first]}
    ranked = [*first, *(c for c in short.ranked if c.product_id not in taken)]
    new, recheck_notes = recheck_picks(ranked, k - len(kept), refetch_and_keep, needs.budget_sek(),
                                       needs.rank_conditions())
    chosen = [*kept, *new]
    card_notes = {c.listing_id: short.size_notes[c.listing_id] for c in chosen if c.listing_id in short.size_notes}
    notes = short.notes + recheck_notes
    texts, errors = None, []
    if chosen and llm is not None:
        try:
            doubts = check_sizes([(c, fetched[c.listing_id]) for c in chosen if c.listing_id in fetched], llm)
            for listing_id, doubt in doubts.items():
                card_notes[listing_id] = f"{card_notes[listing_id]} {doubt}" if listing_id in card_notes else doubt
        except (LLMUnavailable, LLMOutputError) as exc:
            notes.append(f"Size check skipped: {exc}")
        try:
            texts, errors = write_picks(llm, chosen, needs.text(), language)
        except LLMUnavailable as exc:
            notes.append(f"No summaries: {exc}")
    notes += [card_notes[c.listing_id] for c in chosen if c.listing_id in card_notes]
    return PicksResult(chosen, texts, errors, notes, short.dropped, needs.text(), card_notes)


def refetcher(adapter, conn: psycopg.Connection, category: str, on_problem: OnProblem) -> Refetch:
    def refetch(card: Card) -> ProductRecord | None:
        page = fetch_checked(adapter, conn, card.url, on_problem)
        if page is None:
            return None
        rec = adapter.parse(page)
        save_record(conn, rec, category)
        return rec

    return refetch
