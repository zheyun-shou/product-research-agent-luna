"""Pros and complaints of each pick from its buyer reviews (spec 4.7, phase 3 Task 7). The statements are qualitative
and cite the review chunks that they use. Python checks the citations, and that each number is in a cited chunk."""
from typing import Callable

from pydantic import BaseModel

from scout.llm.client import LLMOutputError, LLMUnavailable
from scout.llm.feedback import retry_prompt
from scout.picks.rank import Card
from scout.picks.write import numbers_in

PER_QUERY = 3  # chunks for each complaint type and for the praise
MIN_REVIEWS = 3  # fewer review chunks than this give no summary

SYSTEM = """You summarize what buyers say about one product, from parts of their reviews.
Rules:
- Write one to three pros and one to three complaints. Use an empty list when the reviews give none.
- Each statement is short and qualitative. Say how common it is with words such as "most buyers", "many",
  "some", "a few" or "one buyer". Never count reviews and never give percentages.
- When the reviews disagree, say so, for example "most say it is quiet, a few say it hums at night".
- The reviews were chosen to show both sides, so critical reviews are over-represented. Judge how common a
  complaint is with the overall rating and the number of ratings in mind.
- Use only the reviews you are given. Each statement cites in "sources" the ids of the reviews it uses.
- Do not write numbers that are not in the reviews you cite.
- Write in {language}. The reviews can be in other languages."""

PRAISE_QUERY = "what buyers like and praise about the product"


class Point(BaseModel):
    text: str
    sources: list[int]


class PickOpinions(BaseModel):
    pros: list[Point]
    complaints: list[Point]


def build_prompt(card: Card, chunks: dict[int, str]) -> str:
    if card.rating is None:
        rating = "no overall rating"
    elif card.review_count:
        rating = f"{card.rating} of 5 from {card.review_count} ratings"
    else:
        rating = f"{card.rating} of 5 (the number of ratings is not known)"
    lines = [f"Product: {card.title}", f"Overall rating: {rating}", "", "Reviews:"]
    lines += [f"[{cid}] {text}" for cid, text in chunks.items()]
    return "\n".join(lines)


def check_opinions(opinions: PickOpinions, chunks: dict[int, str]) -> list[str]:
    errors = []
    for kind, points in (("pro", opinions.pros), ("complaint", opinions.complaints)):
        for point in points:
            if not point.sources:
                errors.append(f"the {kind} '{point.text[:60]}' cites no review")
                continue
            unknown = [s for s in point.sources if s not in chunks]
            if unknown:
                errors.append(f"the {kind} '{point.text[:60]}' cites {unknown}, which are not reviews of this product. "
                              f"Use only these ids: {sorted(chunks)}")
                continue
            cited = set().union(*(numbers_in(chunks[s]) for s in point.sources))
            extra = sorted(numbers_in(point.text) - cited)
            if extra:
                errors.append(f"the {kind} '{point.text[:60]}' has the numbers {[f'{n:f}' for n in extra]}, which "
                              "are not in the reviews it cites. Remove them or cite the review that has them.")
    return errors


def retrieve_reviews(card: Card, complaint_types: list[str], search: Callable[[str, int], list]) -> dict[int, str]:
    """The review chunks for each complaint type and for the praise, without repeats."""
    chunks: dict[int, str] = {}
    for query in [*complaint_types, PRAISE_QUERY]:
        for hit in search(query, PER_QUERY):
            chunks.setdefault(hit.chunk_id, hit.text.split("\n", 1)[-1])  # without the "Product: ..." line
    return chunks


def write_opinions(llm, card: Card, chunks: dict[int, str], language: str = "English",
                   max_rounds: int = 3) -> tuple[PickOpinions | None, str]:
    """The checked pros and complaints of one pick, and "" or why there are none: "checks" when no round passed the
    checks, "format" when the model never gave a usable reply. LLMUnavailable goes to the caller."""
    base = build_prompt(card, chunks)
    opinions, errors, checked = None, [], False
    for _ in range(max_rounds):
        try:
            opinions = llm.structured("chat", SYSTEM.format(language=language), retry_prompt(base, opinions, errors),
                                      PickOpinions)
        except LLMOutputError:
            continue
        checked = True
        errors = check_opinions(opinions, chunks)
        llm.mark_checked(not errors, errors)
        if not errors:
            return opinions, ""
    return None, "checks" if checked else "format"


WHY_NONE = {
    "few": "Too few reviews to summarize.",
    "checks": "The summary did not pass the checks, so it is not shown.",
    "format": "The model did not give a usable summary.",
}


def opinions_text(n: int, card: Card, opinions: PickOpinions | None, why: str = "") -> list[str]:
    lines = [f"{n}. {card.title[:80]}"]
    if opinions is None:
        lines.append(f"   {WHY_NONE[why]}")
        return lines
    lines += [f"   + {p.text}" for p in opinions.pros]
    lines += [f"   - {p.text}" for p in opinions.complaints]
    if not opinions.pros and not opinions.complaints:
        lines.append("   The reviews give no clear pros or complaints.")
    return lines


def pick_opinions(llm, picks: list[tuple[int, Card]], complaint_types: list[str],
                  search_for: Callable[[Card], Callable[[str, int], list]], language: str = "English"
                  ) -> tuple[str, dict[int, dict]]:
    """picks are (the number that the shopper saw, card). Returns the text for the shopper ("" when no pick has
    enough reviews), and for each listing its checked pros and complaints with their sources. When the model is not
    available, the summaries written before stay."""
    lines = ["What buyers say (buyer reviews from amazon.se):"]
    stored: dict[int, dict] = {}
    useful, stopped = False, ""
    for n, card in picks:
        chunks = retrieve_reviews(card, complaint_types, search_for(card))
        if len(chunks) < MIN_REVIEWS:
            lines += opinions_text(n, card, None, "few")
            continue
        try:
            opinions, why = write_opinions(llm, card, chunks, language)
        except LLMUnavailable as exc:
            stopped = f"The other picks have no summary now. {exc}"
            break
        useful = True
        if opinions is not None:
            stored[card.listing_id] = opinions.model_dump()
        lines += opinions_text(n, card, opinions, why)
    if stopped:
        lines.append(stopped)
    return ("\n".join(lines) if useful or stopped else ""), stored
