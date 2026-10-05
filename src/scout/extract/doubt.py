"""The worker compares the sizes of each pick with the rest of its page. Sellers sometimes give wrong sizes."""
from pydantic import BaseModel, Field

from scout.extract.verify import verify_raw
from scout.picks.rank import SIZES, Card
from scout.sources.base import ProductRecord

SYSTEM = """You check the sizes of products on amazon.se pages. Sellers sometimes give wrong sizes:
the size of the box, the size of another model, or height and depth in the wrong order.
For each product, compare the sizes with the title, the bullet points and the specifications.
Report a size only when something contradicts it: a text on the page, or a clear fact about this kind of product.
For example, a 90 l fridge with a freezer part is not 48 cm high.
When a text on the page contradicts the size, copy that text exactly into evidence. Otherwise leave evidence empty.
Most products have no problem. Return no doubt for them."""


class SizeDoubt(BaseModel):
    product: int
    sizes: list[str]
    reason: str
    evidence: str = ""


class SizeCheck(BaseModel):
    doubts: list[SizeDoubt] = Field(default_factory=list)


def page_text(rec: ProductRecord) -> str:
    specs = "\n".join(f"{k}: {v}" for k, v in rec.specs.items())
    return "\n".join([rec.title or "", *rec.bullets, specs])


def product_block(n: int, card: Card, rec: ProductRecord) -> str:
    facts = ", ".join(f"{k}={v.get('value')} {v.get('unit') or ''}".strip() for k, v in card.attributes.items())
    return f"Product {n}: {card.title}\nFacts: {facts}\nPage:\n{page_text(rec)[:3000]}"


def check_sizes(items: list[tuple[Card, ProductRecord]], llm) -> dict[int, str]:
    """Mark doubtful sizes on the cards and return a note for each card. The values never change."""
    checked = [(card, rec) for card, rec in items if any(s in card.attributes for s in SIZES)]
    if not checked:
        return {}
    prompt = "\n\n".join(product_block(n, card, rec) for n, (card, rec) in enumerate(checked, 1))
    notes: dict[int, str] = {}
    for doubt in llm.structured("worker", SYSTEM, prompt, SizeCheck).doubts:
        if not 1 <= doubt.product <= len(checked):
            continue
        card, rec = checked[doubt.product - 1]
        sizes = [s for s in SIZES if s in doubt.sizes and s in card.attributes]
        if not sizes:
            continue
        if doubt.evidence and not verify_raw(doubt.evidence, page_text(rec) + "\n" + rec.text):
            continue  # the quote is not on the page, so the doubt has no support
        source = f'the page says "{doubt.evidence}"' if doubt.evidence else "judged by the worker model"
        card.attributes = {k: ({**v, "doubtful": True} if k in sizes else v) for k, v in card.attributes.items()}
        notes[card.listing_id] = f"{card.title[:60]}: {', '.join(sizes)} may be wrong ({doubt.reason}; {source})."
    return notes
