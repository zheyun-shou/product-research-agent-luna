"""On-demand attribute extraction (spec 4.4): a condition needs an attribute that a candidate does not have. Retrieve
the chunks about it from the text of that candidate, let the worker read the value, and keep it only when its raw
text is in the cited chunk and shows the value."""
from dataclasses import dataclass
from typing import Callable

from pydantic import BaseModel

from scout.extract.page import convert, shows_value
from scout.extract.verify import verify_raw
from scout.llm.client import LLMOutputError, LLMUnavailable
from scout.picks.rank import Card, Condition
from scout.profiles import CategoryProfile, ProfileAttribute

MAX_CARDS = 15  # candidates for each picks run, so one run makes at most this many worker calls
CHUNKS = 5  # chunks for each attribute
MAX_CHUNKS = 10  # chunks for each candidate
KINDS = ["specs", "description", "image_text"]  # buyers' reviews can give sizes, but not reliably

SYSTEM = """You read parts of the text of one product from a shop and find the values of the requested fields.
Rules:
- Use only the parts you are given. Never guess, and never use what you know about the product.
- For each field, copy into "raw" the exact short piece of text the value comes from, character for character.
  Include the label and the unit, for example "Höjd: 1780 mm" or "80D x 45B x 48H centimeter".
  Never give a bare number.
- Give in "chunk_id" the id of the part that holds the raw text.
- Give numbers with a dot as decimal separator, converted to the unit asked for. 1780 mm is 178 when the unit is cm.
- If no part states a field, set value, raw and chunk_id to null."""


class FoundValue(BaseModel):
    name: str
    value: str | None = None
    raw: str | None = None
    chunk_id: int | None = None


class Found(BaseModel):
    values: list[FoundValue]


@dataclass
class Store:
    """The database parts that the extraction uses. Tests give fakes."""
    # (product_id, query, k) -> chunks with chunk_id, listing_id, ref and text, from all listings of the product
    retrieve: Callable[[int, str, int], list]
    save: Callable[[int, str, dict], None]  # (product_id, attribute, entry)
    searched: Callable[[list[int]], set[tuple[int, str]]]  # (product_id, attribute) searched on the current text
    mark: Callable[[int, str, bool], None]  # (product_id, attribute, found)


def query_for(attr: ProfileAttribute) -> str:
    return f"{attr.label} ({attr.unit})" if attr.unit else attr.label


def build_prompt(card: Card, attrs: list[ProfileAttribute], chunks: dict[int, str]) -> str:
    lines = [f"Product: {card.title}", "", "Fields:"]
    lines += [f"- {a.name}: {a.label}, number in {a.unit}" if a.unit else f"- {a.name}: {a.label}, number"
              for a in attrs]
    lines += ["", "Parts:"]
    lines += [f"[chunk {cid}]\n{text}\n" for cid, text in chunks.items()]
    return "\n".join(lines)


def fill_missing(cards: list[Card], conditions: list[Condition], profile: CategoryProfile, llm, store: Store,
                 max_cards: int = MAX_CARDS) -> list[str]:
    """Find the missing condition attributes of the first max_cards cards that still need a search. The found values
    go into card.attributes and the database. Returns notes for the picks."""
    wanted = [a for a in (profile.attribute(c.attribute) for c in conditions) if a is not None and a.type == "number"]
    wanted = list({a.name: a for a in wanted}.values())
    if not wanted:
        return []
    missing = {c.listing_id: [a for a in wanted if (c.attributes.get(a.name) or {}).get("value") is None]
               for c in cards}
    todo = [c for c in cards if missing[c.listing_id]]
    done = store.searched(sorted({c.product_id for c in todo})) if todo else set()
    work = []
    for card in todo:
        attrs = [a for a in missing[card.listing_id] if (card.product_id, a.name) not in done]
        if attrs:
            work.append((card, attrs))
    found_count, tried, stopped = 0, 0, ""
    implausible: list[str] = []
    for card, attrs in work[:max_cards]:
        hits = store.retrieve(card.product_id, "; ".join(query_for(a) for a in attrs),
                              min(MAX_CHUNKS, CHUNKS * len(attrs)))
        chunks = {hit.chunk_id: hit for hit in hits}
        if not chunks:
            for attr in attrs:
                store.mark(card.product_id, attr.name, False)
            continue
        try:
            result = llm.structured("worker", SYSTEM, build_prompt(card, attrs, {i: h.text for i, h in chunks.items()}),
                                    Found)
        except LLMUnavailable as exc:
            stopped = f" Stopped early: {exc}"
            break
        except LLMOutputError:
            continue  # this candidate is searched again next time
        tried += 1
        by_name = {attr.name: attr for attr in attrs}
        kept: set[str] = set()
        for item in result.values:
            attr = by_name.get(item.name)
            if attr is None or item.name in kept or item.value is None or not item.raw or item.chunk_id not in chunks:
                continue
            hit = chunks[item.chunk_id]
            value = convert(attr, item.value)
            if value is None or not verify_raw(item.raw, hit.text) or not shows_value(attr, value, item.raw):
                continue
            if not attr.is_plausible(value):
                implausible.append(f"{card.title[:60]}: {attr.label.lower()} {value:g} {attr.unit or ''}".rstrip())
                continue
            # Chunk ids change when a newer page is stored, so the listing and the ref name the place too
            entry = {"value": value, "unit": attr.unit, "raw": item.raw, "source_chunk_id": item.chunk_id,
                     "source_listing_id": hit.listing_id, "source_ref": hit.ref, "source_url": card.url}
            card.attributes = {**card.attributes, attr.name: entry}
            store.save(card.product_id, attr.name, entry)
            kept.add(attr.name)
            found_count += 1
        for attr in attrs:
            store.mark(card.product_id, attr.name, attr.name in kept)
    if not tried and not stopped:
        return []
    notes = [f"Looked for missing values of your conditions in the text of {tried} products: found {found_count}."
             + stopped]
    if implausible:
        notes.append("Left out because a real product cannot have them: " + "; ".join(dict.fromkeys(implausible)) + ".")
    return notes
