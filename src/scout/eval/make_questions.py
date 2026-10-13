"""Make the question set of the retrieval evaluation (phase 3 Task 8, my decision 3 of 2026-10-06): an LLM writes
the questions from the stored chunks and copies the exact words that hold each answer; Python keeps a gold label
only when the words are in its chunk. The set is "made by an LLM, checked by Python", not labelled by hand.

Two kinds of questions:
- one product: the model sees all chunks of one product and writes questions that a shopper could ask a search over
  all stored products of the category. The scope is the whole category, so the search must find the product first.
- across products: the model sees the chunks of 3 products of one category and writes one question about all
  three.
  The scope is the three products. Each chunk that answers is gold."""
import random
from dataclasses import dataclass

from pydantic import BaseModel, Field

LANGUAGES = ("sv", "en", "zh")
STYLES = {
    "model": "Name the model number of the product, as a shopper who knows it.",
    "brand": "Name the brand and the kind of product, but not the model number.",
    "no_name": "Do not name the brand or the model. Describe the product by what makes it different, so that only "
               "this product of the category fits.",
}
LANGUAGE_NAMES = {"sv": "Swedish", "en": "English", "zh": "Chinese"}
MIN_CHUNKS = 5  # a product with fewer chunks gives too little to ask about
QUOTE_WORDS = "5 to 15 words"

SYSTEM = f"""You write test questions for the text search of a shopping assistant. The search looks through the
stored text of products from amazon.se: specifications, descriptions and buyer reviews, cut into numbered chunks.

Rules:
- Each question must be answered by the chunks that you get. Do not ask about the price or the stock.
- For each question, give every chunk that holds the answer: its number and {QUOTE_WORDS} copied exactly from that
  chunk, without any change. The words must show the answer.
- Write natural questions that a shopper could type, in the language that is asked for. The chunks can be in
  another language.
- Ask about different things: a size, a feature, what buyers say about noise, frost, the door, the delivery or the
  quality after some time."""


class GoldOut(BaseModel):
    chunk: int = Field(description="The number of the chunk")
    quote: str = Field(description=f"{QUOTE_WORDS} copied exactly from the chunk")


class QuestionOut(BaseModel):
    question: str
    gold: list[GoldOut]


class QuestionSet(BaseModel):
    questions: list[QuestionOut]


@dataclass
class Source:
    """One stored chunk that the model sees, with its number in the prompt."""
    number: int
    asin: str
    ref: str
    kind: str
    text: str


def products(conn, min_chunks: int = MIN_CHUNKS) -> list[dict]:
    """The amazon.se products with enough chunks: asin, title, category, chunk count."""
    return conn.execute(
        """SELECT l.source_id AS asin, l.title, p.category, count(c.id) AS chunks
           FROM listings l JOIN products p ON p.id = l.product_id JOIN chunks c ON c.listing_id = l.id
           WHERE l.source = 'amazon_se'
           GROUP BY l.source_id, l.title, p.category
           HAVING count(c.id) >= %s ORDER BY l.source_id""", (min_chunks,)).fetchall()


def sources(conn, asins: list[str], kinds: list[str] | None = None) -> list[Source]:
    rows = conn.execute(
        """SELECT l.source_id AS asin, c.ref, c.kind, c.text FROM chunks c JOIN listings l ON l.id = c.listing_id
           WHERE l.source_id = ANY(%s) AND (%s::text[] IS NULL OR c.kind = ANY(%s)) ORDER BY l.source_id, c.id""",
        (asins, kinds, kinds)).fetchall()
    return [Source(n, r["asin"], r["ref"], r["kind"], r["text"]) for n, r in enumerate(rows, 1)]


def chunk_text(items: list[Source], names: dict[str, str]) -> str:
    return "\n\n".join(f"[{s.number}] ({names[s.asin]}, {s.kind})\n{s.text}" for s in items)


def one_product_prompt(title: str, items: list[Source], language: str, style: str, n: int) -> str:
    return (f"The product: {title}\n\nWrite {n} questions in {LANGUAGE_NAMES[language]}. {STYLES[style]}\n\n"
            + chunk_text(items, {items[0].asin: "this product"}))


def across_prompt(titles: dict[str, str], items: list[Source], language: str) -> str:
    names = {asin: f"product {i}" for i, asin in enumerate(titles, 1)}
    head = "\n".join(f"{names[a]}: {t}" for a, t in titles.items())
    return (f"The products:\n{head}\n\nWrite 1 question in {LANGUAGE_NAMES[language]} that asks about all three "
            "products at once, for example which of them buyers call quiet, or what they say about the frost. "
            "Give every chunk of any of the products that holds a part of the answer.\n\n"
            + chunk_text(items, names))


def checked(out: QuestionOut, items: list[Source]) -> tuple[list[dict], list[str]]:
    """The gold labels whose words are in their chunk, and the problems of the others."""
    by_number = {s.number: s for s in items}
    gold, problems = [], []
    for g in out.gold:
        source = by_number.get(g.chunk)
        quote = g.quote.strip()
        if source is None:
            problems.append(f"no chunk [{g.chunk}]")
        elif not quote or quote not in source.text:
            problems.append(f"the words '{quote[:60]}' are not in chunk [{g.chunk}]")
        elif not any(x["asin"] == source.asin and x["ref"] == source.ref for x in gold):
            gold.append({"asin": source.asin, "ref": source.ref, "quote": quote})
    return gold, problems


@dataclass
class Made:
    questions: list[dict]
    problems: list[str]
    asked: int = 0  # questions that the model wrote


def make_questions(conn, llm, one: int = 12, per_product: int = 2, across: int = 6, seed: int = 7,
                   role: str = "chat", min_chunks: int = MIN_CHUNKS, say=print) -> Made:
    """one products with per_product questions each, and across groups of 3 products of one category. The groups
    do not share a product."""
    rng = random.Random(seed)
    pool = products(conn, min_chunks)
    made = Made([], [])

    def add(out: QuestionOut, items, language, style, category, asins):
        made.asked += 1
        qid = f"q{made.asked:02d}"
        gold, problems = checked(out, items)
        made.problems += [f"{qid}: {p}" for p in problems]
        if gold:
            made.questions.append({"id": qid, "question": out.question.strip(), "language": language,
                                   "style": style, "category": category, "asins": asins, "gold": gold})
        else:
            made.problems.append(f"{qid}: not used, no gold label is right")

    for i, product in enumerate(rng.sample(pool, min(one, len(pool)))):
        language, style = LANGUAGES[i % len(LANGUAGES)], list(STYLES)[i % len(STYLES)]
        items = sources(conn, [product["asin"]])
        say(f"{product['asin']}: {per_product} questions in {language}, style {style}")
        result = llm.structured(role, SYSTEM, one_product_prompt(product["title"], items, language, style,
                                                                 per_product), QuestionSet)
        for out in result.questions[:per_product]:
            add(out, items, language, style, product["category"], [])

    by_category: dict[str, list[dict]] = {}
    for product in pool:
        by_category.setdefault(product["category"], []).append(product)
    groups = []
    for members in by_category.values():
        members = rng.sample(members, len(members))
        groups += [members[n:n + 3] for n in range(0, len(members) - 2, 3)]
    for i, group in enumerate(rng.sample(groups, min(across, len(groups)))):
        language = LANGUAGES[i % len(LANGUAGES)]
        titles = {p["asin"]: p["title"] for p in group}
        items = sources(conn, list(titles), ["review", "specs"])
        say(f"{', '.join(titles)}: 1 question across them in {language}")
        result = llm.structured(role, SYSTEM, across_prompt(titles, items, language), QuestionSet)
        for out in result.questions[:1]:
            add(out, items, language, "across", group[0]["category"], list(titles))
    return made
