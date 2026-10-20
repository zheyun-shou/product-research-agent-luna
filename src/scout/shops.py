"""read_page: one product page of a shop that is not amazon.se (design section 1a). The page data (JSON-LD) first,
then the worker with the raw-text check for what is missing. The product goes into the database like an amazon.se
product, as a listing of the same product when the GTIN, or the brand and the model, are the same."""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

import psycopg

from scout.db.store import (find_product, log_fetch, product_review_texts, same_brand_listings, save_record,
                             save_review_read)
from scout.extract.verify import normalise
from scout.fetch import Fetched, site_of
from scout.sources.base import ProductRecord
from scout.shop_reviews import more_reviews
from scout.sources.generic import read_generic

AMAZON = "amazon.se"
AMAZON_NOTE = "amazon.se pages come through search_shop"


@dataclass
class PageRead:
    text: str  # the report for the chat model
    pages: int  # pages loaded
    listing_id: int | None = None


def read_record(fetched: Fetched, enrich: Callable[[ProductRecord], tuple[ProductRecord, bool]] | None,
                attributes: bool = True) -> tuple[ProductRecord | None, list[str], str]:
    """The record of a loaded page, the fields from its page data, and a problem. The worker reads the page text
    for the missing core fields, and for the attributes of the profile unless `attributes` is False."""
    if fetched.page is None:
        return None, [], fetched.problem
    if is_amazon(fetched.page.final_url):  # a short link or a "go to the shop" link
        return None, [], f"the address leads to amazon.se, and {AMAZON_NOTE}"
    rec, from_data = read_generic(fetched.page)
    if enrich is not None and (attributes or rec.price is None or rec.in_stock is None):
        rec, _ = enrich(rec)
    # A test or a blog can have product data without an offer: only a page with a price, or sold out, is a shop page
    if not rec.title or (rec.price is None and rec.in_stock is not False):
        return None, from_data, "the page shows no product with a price"
    return rec, from_data, ""


def is_amazon(url: str) -> bool:
    site = site_of(url)
    return site == AMAZON or site.endswith("." + AMAZON)


def known_reviews(conn: psycopg.Connection, rec: ProductRecord, category: str) -> set[str]:
    """The review texts of the product in its other listings. A page read again keeps its own reviews."""
    row = conn.execute("SELECT id, product_id FROM listings WHERE source = %s AND source_id = %s",
                       (rec.source, rec.source_id)).fetchone()
    product_id = row["product_id"] if row else find_product(conn, rec, category)
    return product_review_texts(conn, product_id, but=row["id"] if row else None) if product_id else set()


def unique_reviews(rec: ProductRecord, known: set[str]) -> tuple[ProductRecord, int]:
    """The reviews of the page that the product does not have yet in another shop, and how many were left out."""
    kept, seen = [], set(known)
    for review in rec.reviews:
        key = normalise(review.get("text") or "")
        if key and key not in seen:
            kept.append(review)
            seen.add(key)
    return rec.model_copy(update={"reviews": kept}), len(rec.reviews) - len(kept)


def read_page(conn: psycopg.Connection, fetch: Callable[[str], Fetched], url: str, category: str,
              enrich: Callable[[ProductRecord], tuple[ProductRecord, bool]] | None,
              more: Callable[[str], list[dict]] = more_reviews) -> PageRead:
    """more(url): the reviews that the page loads apart from its page data (scout.shop_reviews)."""
    site = site_of(url)
    if is_amazon(url):
        return PageRead(AMAZON_NOTE + ".", 0)
    fetched = fetch(url)
    page = fetched.page
    rec, from_data, problem = read_record(fetched, enrich)
    log_fetch(conn, site, url, "ok" if not problem else "failed", page.status if page else None,
              page.duration_ms if page else None, problem)
    if rec is None:
        return PageRead(f"{url}: not read, {problem}.", 1)
    extra = more(page.final_url)
    if extra:
        rec = rec.model_copy(update={"reviews": [*rec.reviews, *extra]})
    rec, copies = unique_reviews(rec, known_reviews(conn, rec, category))
    ids = save_record(conn, rec, category)
    save_review_read(conn, ids.listing_id, datetime.now(timezone.utc), complete=True)
    others = conn.execute("SELECT id, source FROM listings WHERE product_id = %s AND id <> %s ORDER BY id",
                          (ids.product_id, ids.listing_id)).fetchall()
    text = report(ids.listing_id, rec, from_data, copies, others, fetched.via)
    maybe = same_brand_listings(conn, ids.product_id, f"{rec.title} {rec.model or ''}") if not others else []
    if maybe:
        text += ("\nStored products of the same brand in other shops: "
                 + "; ".join(f"id {m['id']} {m['title']} ({m['source']})" for m in maybe)
                 + ". If one is the same product (the same model, written in another way), call same_product.")
    return PageRead(text, 1, ids.listing_id)


def report(listing_id: int, rec: ProductRecord, from_data: list[str], copies: int, others: list[dict],
           via: str) -> str:
    stock = {True: "yes", False: "no", None: "not known"}[rec.in_stock]
    price = f"{rec.price} {rec.currency or ''}".strip() if rec.price is not None else "not shown"
    rating = f"{rec.rating} of 5 ({rec.review_count} ratings)" if rec.rating is not None else "none"
    lines = [f"id {listing_id}: {rec.title} ({rec.source}) | price={price} | in_stock={stock} | rating={rating} | "
             f"brand={rec.brand or 'not known'} | model={rec.model or 'not known'}" + (f" | GTIN {rec.gtin}"
                                                                                      if rec.gtin else ""),
             f"From the page data: {', '.join(from_data) or 'nothing'}."
             + (f" From the page text through the worker: {', '.join(rec.llm_fields)}." if rec.llm_fields else "")]
    if rec.attributes:
        lines.append("Attributes: " + ", ".join(f"{k}={v['value']} {v.get('unit') or ''}".strip()
                                                 for k, v in rec.attributes.items()))
    if rec.specs:
        lines.append(f"{len(rec.specs)} specifications stored; read_product gives them.")
    lines.append(f"{len(rec.reviews)} buyer reviews from this page stored"
                 + (f"; {copies} were left out, because another shop has the same text" if copies else "") + ".")
    if others:
        lines.append("The same product as " + ", ".join(f"id {o['id']} ({o['source']})" for o in others) + ".")
    if via == "browser":
        lines.append("The page needed the browser.")
    if site_of(rec.url) == "prisjakt.nu":
        lines.append("Prisjakt compares prices: the price is the lowest offer of the shops that it lists, and its "
                     "buyer reviews come from Prisjakt in several countries (the market is in the title).")
    lines.append(rec.url)
    return "\n".join(lines)


def refetch(conn: psycopg.Connection, fetch: Callable[[str], Fetched], url: str, category: str,
            enrich: Callable[[ProductRecord], tuple[ProductRecord, bool]] | None) -> ProductRecord | None:
    """The live check of a pick from another shop: its page again, for the price and the stock now."""
    fetched = fetch(url)
    rec, _, problem = read_record(fetched, enrich, attributes=False)
    page = fetched.page
    log_fetch(conn, site_of(url), url, "ok" if not problem else "failed", page.status if page else None,
              page.duration_ms if page else None, problem)
    if rec is None:
        return None
    rec, _ = unique_reviews(rec, known_reviews(conn, rec, category))
    save_record(conn, rec, category)
    return rec
