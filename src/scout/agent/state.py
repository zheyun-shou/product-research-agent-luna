"""The state of one conversation thread. It keeps product IDs, never full product records."""
import operator
from typing import Annotated, TypedDict


class ChatState(TypedDict, total=False):
    messages: Annotated[list[dict], operator.add]
    summary: str
    summarized: int    # messages before this index are in the summary
    phase: str         # clarify, search, recommend or picks
    needs: dict        # Needs as JSON
    picks: list[dict]  # listing_id, product_id, title, doubtful sizes and the note of each pick, in the order shown
    seen: list[int]    # product IDs shown since the needs last changed; "more" leaves them out
    searches: dict     # search terms -> {"page": the last result page read, "budget": the lowest budget of those reads}
    removed: list[dict]  # product_id, listing_id and title of each product that the shopper removed
    opinions: dict     # listing_id (as text) -> the checked pros and complaints of that pick, with their sources
    route: dict        # what the follow-up tools of this turn did, and the next step
