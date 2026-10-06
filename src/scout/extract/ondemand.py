"""On-demand attribute extraction (spec 4.4): a condition needs an attribute that a candidate does not have. Retrieve
the chunks about it from the text of that candidate, let the worker read the value, and keep it only when its raw
text is in the cited chunk and shows the value."""
from dataclasses import dataclass
from typing import Callable

from pydantic import BaseModel

from scout.extract.page import convert, shows_value
from scout.extract.verify import verify_raw
from scout.picks.rank import Card, Condition
from scout.profiles import CategoryProfile, ProfileAttribute

MAX_CARDS = 15  # candidates for each picks run, so one run makes at most this many worker calls
CHUNKS = 5  # chunks for each attribute
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
    retrieve: Callable[[int, str, int], list]  # (listing_id, query, k) -> chunks with chunk_id and text
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
    """Find the missing condition attributes of the first max_cards cards that miss one. The found values go into
    card.attributes and the database. Returns notes for the picks."""
    wanted = {c.attribute for c in conditions}
    todo = [c for c in cards if wanted - set(k for k, v in c.attributes.items() if (v or {}).get("value") is not None)]
    todo = todo[:max_cards]
    if not todo:
        return []
    done = store.searched(sorted({c.product_id for c in todo}))
    found_count, tried = 0, 0
    for card in todo:
        attrs = [a for a in (profile.attribute(n) for n in sorted(wanted))
                 if a is not None and a.type == "number"
                 and (card.attributes.get(a.name) or {}).get("value") is None
                 and (card.product_id, a.name) not in done]
        if not attrs:
            continue
        chunks: dict[int, str] = {}
        for attr in attrs:
            for hit in store.retrieve(card.listing_id, query_for(attr), CHUNKS):
                chunks.setdefault(hit.chunk_id, hit.text)
        if not chunks:
            for attr in attrs:
                store.mark(card.product_id, attr.name, False)
            continue
        tried += 1
        result = llm.structured("worker", SYSTEM, build_prompt(card, attrs, chunks), Found)
        by_name = {attr.name: attr for attr in attrs}
        kept: set[str] = set()
        for item in result.values:
            attr = by_name.get(item.name)
            if attr is None or item.name in kept or item.value is None or not item.raw or item.chunk_id not in chunks:
                continue
            value = convert(attr, item.value)
            if value is None or not verify_raw(item.raw, chunks[item.chunk_id]) \
                    or not shows_value(attr, value, item.raw):
                continue
            entry = {"value": value, "unit": attr.unit, "raw": item.raw, "source_chunk_id": item.chunk_id,
                     "source_url": card.url}
            card.attributes = {**card.attributes, attr.name: entry}
            store.save(card.product_id, attr.name, entry)
            kept.add(attr.name)
            found_count += 1
        for attr in attrs:
            store.mark(card.product_id, attr.name, attr.name in kept)
    if not tried:
        return []
    return [f"Looked for missing values of your conditions in the text of {tried} products: found {found_count}."]
