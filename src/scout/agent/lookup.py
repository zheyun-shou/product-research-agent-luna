"""Tools that look things up. Python runs them and gives the result back to the chat model in the same turn."""
import base64
from dataclasses import dataclass
from typing import Callable, Literal

import httpx
from pydantic import BaseModel, Field

from scout.needs import ConditionSpec, Needs
from scout.picks.rank import Card
from scout.picks.write import card_text
from scout.profiles import CategoryProfile
from scout.text.retrieve import Hit, Scope

MAX_PRODUCT_CHARS = 12_000  # of one product's text in the prompt
MAX_IMAGES = 6
SEARCH_HITS = 10
IMAGE_HOSTS = ("https://m.media-amazon.com/",)  # the image server of amazon.se, not its pages


class ReadProduct(BaseModel):
    """Read the full page text of picks: the specifications, the bullet points, the seller's description and the
    reviews on the product page. Use it for a question that the cards do not answer."""
    picks: list[int] = Field(description="Pick numbers, at most 3")
    question: str = Field(description="What you look for, for example 'a timer' or 'what buyers say about noise'")


class QueryProducts(BaseModel):
    """Find stored products of this category that match conditions, also products that are not in the picks.
    Use it to answer a question such as 'is there one under 4000 SEK with No Frost?'. It does not change the picks:
    to show other products as picks, call next_step change_needs."""
    kind: str | None = Field(None, description="A kind of the category, or null for the kind of the shopper")
    conditions: list[ConditionSpec] = Field(default_factory=list, description="Number conditions, for example "
                                                                             "height <= 180")
    budget: float | None = Field(None, description="Highest price in SEK, or null for the shopper's budget")
    limit: int = Field(10, description="At most this many products, at most 20")


class ReadImages(BaseModel):
    """Look at the product images of one pick, for a fact that only an image shows, for example a size drawing or
    the inside of a fridge. Prices in images are often old, so never read a price from an image."""
    pick: int
    question: str


class SearchText(BaseModel):
    """Search the stored text of many products at once: specifications, descriptions and reviews. It gives the
    10 best matching parts. Use it for a question across products or across many reviews, for example 'which
    picks have reviews about noise'. For all the text of one or two picks, use read_product."""
    question: str = Field(description="What you look for, in any language")
    picks: list[int] = Field(default_factory=list, description="Pick numbers, or empty for all stored products "
                                                                 "of this category")
    kinds: list[Literal["specs", "description", "review"]] = Field(
        default_factory=list, description="Only these kinds of text, or empty for all")


LOOKUP_TOOLS = [ReadProduct, SearchText, QueryProducts, ReadImages]


class ImageAnswer(BaseModel):
    found: bool
    answer: str = Field(description="What the images show about the question. Empty when they do not show it")


IMAGE_SYSTEM = """You look at the images of one product from amazon.se and answer one question about it.
Use only what the images show. Never read a price from an image. If the images do not show the answer, set
found to false. Answer in English, in one to three sentences."""


@dataclass
class Lookup:
    """What the lookup tools need from the rest of the program."""
    profile: CategoryProfile
    needs: Needs
    cards: list[Card]  # the current picks, in order
    page_texts: Callable[[list[int]], dict[int, dict]]
    query: Callable[[CategoryProfile, Needs], list[Card]]
    read_images: Callable[[list[str], str], ImageAnswer]
    search: Callable[[str, Scope, int], list[Hit]] | None = None

    def run(self, call: BaseModel) -> str:
        if isinstance(call, ReadProduct):
            return self.read_product(call)
        if isinstance(call, SearchText):
            return self.search_text(call)
        if isinstance(call, QueryProducts):
            return self.query_products(call)
        return self.images(call)

    def card(self, n: int) -> Card | None:
        return self.cards[n - 1] if 1 <= n <= len(self.cards) else None

    def read_product(self, call: ReadProduct) -> str:
        wanted = [(n, self.card(n)) for n in call.picks[:3]]
        texts = self.page_texts([c.listing_id for _, c in wanted if c])
        parts = []
        for n, card in wanted:
            if card is None:
                parts.append(f"There is no pick {n}.")
            elif card.listing_id not in texts:
                parts.append(f"Pick {n}: no page text is stored for this product.")
            else:
                parts.append(f"Pick {n}: {card.title}\n" + product_text(texts[card.listing_id]))
        return f"read_product ({call.question}):\n\n" + "\n\n".join(parts)

    def search_text(self, call: SearchText) -> str:
        head = f"search_text ({call.question})"
        if self.search is None:
            return f"{head}: the text search is not available now. Use read_product."
        wanted = [self.card(n) for n in call.picks]
        if call.picks and not all(wanted):
            return f"{head}: there are only {len(self.cards)} picks."
        scope = Scope(listing_ids=[c.listing_id for c in wanted] or None, kinds=list(call.kinds) or None,
                      category=self.profile.category)
        try:
            hits = self.search(call.question, scope, SEARCH_HITS)
        except (ImportError, OSError, RuntimeError) as e:
            return f"{head}: the text search failed ({type(e).__name__}). Use read_product."
        in_picks = {c.listing_id: n for n, c in enumerate(self.cards, 1)}
        lines = [f"[{i}] {'pick ' + str(in_picks[h.listing_id]) if h.listing_id in in_picks else 'not a pick'}, "
                 f"{h.ref}:\n{h.text}" for i, h in enumerate(hits, 1)]
        return (f"{head}: the {len(hits)} best matching parts of the stored text, best first. Reviews are a "
                "sample, not all reviews.\n\n" + ("\n\n".join(lines) or "Nothing matches."))

    def query_products(self, call: QueryProducts) -> str:
        known = {a.name for a in self.profile.attributes if a.type == "number"}
        bad = [c.attribute for c in call.conditions if c.attribute not in known]
        if bad:
            return f"query_products: {bad} are not number attributes of the profile. Use {sorted(known)}."
        needs = self.needs.model_copy(update={
            "kind": call.kind or self.needs.kind,
            "conditions": [*self.needs.conditions, *call.conditions],
            "budget": call.budget if call.budget is not None else self.needs.budget,
        })
        found = self.query(self.profile, needs)[:max(1, min(call.limit, 20))]
        in_picks = {c.listing_id: n for n, c in enumerate(self.cards, 1)}
        lines = [card_text(i, c).replace(f"Pick {i}:", f"Result {i}{in_pick(in_picks, c)}:", 1)
                 for i, c in enumerate(found, 1)]
        return (f"query_products ({needs.text() or 'no conditions'}): {len(found)} stored products. They are not "
                "picks.\n" + ("\n".join(lines) or "No stored product matches."))

    def images(self, call: ReadImages) -> str:
        card = self.card(call.pick)
        if card is None:
            return f"read_images: there is no pick {call.pick}."
        urls = (self.page_texts([card.listing_id]).get(card.listing_id) or {}).get("images") or []
        urls = [u for u in urls if u.startswith(IMAGE_HOSTS)][:MAX_IMAGES]
        if not urls:
            return f"read_images: no images are stored for pick {call.pick}."
        answer = self.read_images(urls, call.question)
        return (f"read_images (pick {call.pick}, {call.question}): "
                + (answer.answer if answer.found else "the images do not show it."))


def in_pick(in_picks: dict[int, int], card: Card) -> str:
    return f" (pick {in_picks[card.listing_id]})" if card.listing_id in in_picks else ""


def product_text(row: dict) -> str:
    lines = [f"Specifications: {'; '.join(f'{k}: {v}' for k, v in (row.get('specs') or {}).items()) or 'none'}"]
    lines += [f"- {b}" for b in row.get("bullets") or []]
    if row.get("description"):
        lines.append(f"Description: {row['description']}")
    reviews = row.get("reviews") or []
    lines.append(f"Reviews on the product page ({len(reviews)}, a sample, not all reviews):")
    for r in reviews:
        badge = ", verified purchase" if r.get("verified") else ""
        lines.append(f"- {r.get('rating') or '?'} of 5{badge}, {r.get('date') or 'no date'}: "
                     f"{r.get('title') or ''}. {r.get('text') or ''}")
    return "\n".join(lines)[:MAX_PRODUCT_CHARS]


def fetch_image(url: str, timeout: float = 20) -> str:
    """One image from the image server of amazon.se, as a data URI."""
    response = httpx.get(url, timeout=timeout, follow_redirects=True)
    response.raise_for_status()
    kind = response.headers.get("content-type", "image/jpeg").split(";")[0]
    return f"data:{kind};base64,{base64.b64encode(response.content).decode()}"


def image_reader(llm, fetch=fetch_image) -> Callable[[list[str], str], ImageAnswer]:
    """The worker model reads the images (spec 4.11). PaddleOCR-VL comes in phase 4."""
    def read(urls: list[str], question: str) -> ImageAnswer:
        images = []
        for url in urls:
            try:
                images.append(fetch(url))
            except httpx.HTTPError:
                continue
        if not images:
            return ImageAnswer(found=False)
        return llm.structured("worker", IMAGE_SYSTEM, f"Question: {question}", ImageAnswer, images=images)
    return read
