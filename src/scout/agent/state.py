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
    web_searches: list[str]  # the questions of the web searches in this conversation
    pages_read: list[str]  # the shop pages that read_page stored in this conversation
    removed: list[dict]  # product_id, listing_id and title of each product that the shopper removed
    opinions: dict     # listing_id (as text) -> the checked pros and complaints of that pick, with their sources
    route: dict        # what the follow-up tools of this turn did, and the next step
    # The agent loop (agent/loop.py). Each turn starts them again.
    turn_context: str | None  # the context and the conversation at the start of the turn: the first message
    turn_messages: list[dict]  # the tool calls of the model in this turn and their results, in the chat API format
    used: dict           # tool calls, product pages, review pages, checked listings of this turn
    pending: list[dict]  # tool calls that the model made and Python has not run yet
    final: dict | None   # the final call (show_picks or reply) that waits for the checks
    gate_errors: list[str]  # the problems that the checks found in the last final call
    gate_rounds: int
