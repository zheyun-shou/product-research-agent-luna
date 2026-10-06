"""Write product records and fetch logs to Postgres."""
from dataclasses import dataclass

import psycopg
from psycopg.types.json import Jsonb

from scout.sources.base import Candidate, ProductRecord
from scout.text.chunks import Chunk, page_chunks, review_chunks


@dataclass(frozen=True)
class SavedIds:
    product_id: int
    listing_id: int
    variant_id: int


def save_record(conn: psycopg.Connection, rec: ProductRecord, category: str) -> SavedIds:
    try:
        listing = conn.execute(
            "SELECT id, product_id FROM listings WHERE source = %s AND source_id = %s",
            (rec.source, rec.source_id),
        ).fetchone()
        if listing:
            listing_id, product_id = listing["id"], listing["product_id"]
            conn.execute(
                """UPDATE products SET brand = COALESCE(%s, brand), model = COALESCE(%s, model),
                       attributes = attributes || %s::jsonb
                   WHERE id = %s""",
                (rec.brand, rec.model, Jsonb(rec.attributes), product_id),
            )
            conn.execute(
                """UPDATE listings SET url = %s, title = COALESCE(%s, title), rating = COALESCE(%s, rating),
                       review_count = COALESCE(%s, review_count), fetched_at = %s
                   WHERE id = %s""",
                (rec.url, rec.title, rec.rating, rec.review_count, rec.fetched_at, listing_id),
            )
        else:
            product_id = _find_product(conn, rec, category) or _insert_product(conn, rec, category)
            listing_id = conn.execute(
                """INSERT INTO listings (product_id, source, source_id, url, title, rating, review_count, fetched_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (product_id, rec.source, rec.source_id, rec.url, rec.title, rec.rating, rec.review_count, rec.fetched_at),
            ).fetchone()["id"]
        variant_id = conn.execute(
            """INSERT INTO variants (listing_id, variant_key, label, price, currency, in_stock, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (listing_id, variant_key) DO UPDATE SET
                   label = EXCLUDED.label, price = EXCLUDED.price, currency = EXCLUDED.currency,
                   in_stock = EXCLUDED.in_stock, updated_at = EXCLUDED.updated_at
               RETURNING id""",
            (listing_id, rec.source_id, rec.variant_label, rec.price, rec.currency, rec.in_stock, rec.fetched_at),
        ).fetchone()["id"]
        conn.execute(
            "INSERT INTO price_history (variant_id, price, currency, in_stock, fetched_at) VALUES (%s, %s, %s, %s, %s)",
            (variant_id, rec.price, rec.currency, rec.in_stock, rec.fetched_at),
        )
        save_page_text(conn, listing_id, rec)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return SavedIds(product_id, listing_id, variant_id)


def save_page_text(conn: psycopg.Connection, listing_id: int, rec: ProductRecord) -> None:
    """Keep the text of the newest page. A record without page text (for example from a search page) changes
    nothing."""
    if not (rec.specs or rec.bullets or rec.description or rec.reviews):
        return
    row = conn.execute(
        """INSERT INTO page_texts (listing_id, fetched_at, specs, bullets, description, reviews, images)
           VALUES (%s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (listing_id) DO UPDATE SET fetched_at = EXCLUDED.fetched_at, specs = EXCLUDED.specs,
               bullets = EXCLUDED.bullets, description = EXCLUDED.description, reviews = EXCLUDED.reviews,
               images = EXCLUDED.images
           WHERE page_texts.fetched_at <= EXCLUDED.fetched_at
           RETURNING specs, bullets, description, reviews""",
        (listing_id, rec.fetched_at, Jsonb(rec.specs), Jsonb(rec.bullets), rec.description, Jsonb(rec.reviews),
         Jsonb(rec.images)),
    ).fetchone()
    if row:  # this page is the newest, so its chunks replace the old ones
        save_chunks(conn, listing_id, page_chunks(rec.title or "", dict(row)), rec.fetched_at)


def save_chunks(conn: psycopg.Connection, listing_id: int, chunks: list[Chunk], fetched_at,
                source: str = "product_page") -> None:
    """Replace the chunks of one page kind of a listing. The new chunks have no vectors yet."""
    conn.execute("DELETE FROM chunks WHERE listing_id = %s AND source = %s", (listing_id, source))
    for c in chunks:
        conn.execute(
            """INSERT INTO chunks (listing_id, source, kind, ref, text, meta, fetched_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (listing_id, source, c.kind, c.ref, c.text, Jsonb(c.meta), fetched_at),
        )


def save_shop_reviews(conn: psycopg.Connection, listing_id: int, reviews: list[dict], fetched_at) -> int:
    """Store the reviews of the review pages of one listing, and make their chunks again. A review that the
    product page shows too gets no second chunk. Returns the number of new reviews."""
    try:
        new = 0
        for r in reviews:
            row = conn.execute(
                """INSERT INTO shop_reviews (listing_id, review_id, filter, title, rating, date, verified, variant,
                                             text, fetched_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (listing_id, review_id) DO UPDATE SET filter = EXCLUDED.filter,
                       title = EXCLUDED.title, rating = EXCLUDED.rating, date = EXCLUDED.date,
                       verified = EXCLUDED.verified, variant = EXCLUDED.variant, text = EXCLUDED.text,
                       fetched_at = EXCLUDED.fetched_at
                   RETURNING (xmax = 0) AS inserted""",
                (listing_id, r["review_id"], r["filter"], r.get("title") or "", r.get("rating"), r.get("date") or "",
                 bool(r.get("verified")), r.get("variant") or "", r["text"], fetched_at),
            ).fetchone()
            new += int(row["inserted"])
        stored = conn.execute("SELECT * FROM shop_reviews WHERE listing_id = %s ORDER BY review_id",
                              (listing_id,)).fetchall()
        page = conn.execute(
            """SELECT l.title, p.reviews FROM listings l LEFT JOIN page_texts p ON p.listing_id = l.id
               WHERE l.id = %s""", (listing_id,)).fetchone()
        on_page = {" ".join((r.get("text") or "").split()) for r in (page["reviews"] or [])}
        kept = [dict(r, rating=str(r["rating"]) if r["rating"] is not None else None) for r in stored
                if " ".join(r["text"].split()) not in on_page]
        chunks = review_chunks(page["title"] or "", kept, [f"shop review {r['review_id']}" for r in kept])
        save_chunks(conn, listing_id, chunks, fetched_at, source="review_page")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return new


def save_review_read(conn: psycopg.Connection, listing_id: int, read_at, complete: bool) -> None:
    conn.execute(
        """INSERT INTO review_reads (listing_id, read_at, complete) VALUES (%s, %s, %s)
           ON CONFLICT (listing_id) DO UPDATE SET read_at = EXCLUDED.read_at, complete = EXCLUDED.complete""",
        (listing_id, read_at, complete))
    conn.commit()


def complete_review_reads(conn: psycopg.Connection, listing_ids: list[int]) -> dict[int, object]:
    """The time of the last complete read of the review pages of each listing that has one."""
    rows = conn.execute("SELECT listing_id, read_at FROM review_reads WHERE complete AND listing_id = ANY(%s)",
                        (listing_ids,)).fetchall()
    return {row["listing_id"]: row["read_at"] for row in rows}


def listing_source_ids(conn: psycopg.Connection, source: str, listing_ids: list[int]) -> dict[int, str]:
    rows = conn.execute("SELECT id, source_id FROM listings WHERE source = %s AND id = ANY(%s)",
                        (source, listing_ids)).fetchall()
    return {row["id"]: row["source_id"] for row in rows}


def save_attribute(conn: psycopg.Connection, product_id: int, name: str, entry: dict) -> None:
    conn.execute("UPDATE products SET attributes = attributes || jsonb_build_object(%s::text, %s::jsonb) WHERE id = %s",
                 (name, Jsonb(entry), product_id))
    conn.commit()


def mark_attribute_search(conn: psycopg.Connection, product_id: int, name: str, found: bool) -> None:
    conn.execute(
        """INSERT INTO attribute_searches (product_id, attribute, searched_at, found) VALUES (%s, %s, now(), %s)
           ON CONFLICT (product_id, attribute) DO UPDATE SET searched_at = now(), found = EXCLUDED.found""",
        (product_id, name, found))
    conn.commit()


def attribute_searches(conn: psycopg.Connection, product_ids: list[int]) -> set[tuple[int, str]]:
    """The (product, attribute) pairs that were looked for after the newest text of the product was stored."""
    rows = conn.execute(
        """SELECT a.product_id, a.attribute FROM attribute_searches a
           WHERE a.product_id = ANY(%s)
             AND a.searched_at >= COALESCE((SELECT max(c.fetched_at) FROM chunks c JOIN listings l ON l.id = c.listing_id
                                            WHERE l.product_id = a.product_id), '-infinity'::timestamptz)""",
        (product_ids,)).fetchall()
    return {(row["product_id"], row["attribute"]) for row in rows}


def page_texts(conn: psycopg.Connection, listing_ids: list[int]) -> dict[int, dict]:
    rows = conn.execute("SELECT * FROM page_texts WHERE listing_id = ANY(%s)", (listing_ids,)).fetchall()
    return {row["listing_id"]: dict(row) for row in rows}


# Sellers without a brand use these names. Two listings with such a brand are not the same product.
GENERIC_BRANDS = {"generic", "generisk", "unbranded", "no brand", "okänt märke", "ingen märkning"}


def _find_product(conn: psycopg.Connection, rec: ProductRecord, category: str) -> int | None:
    if not (rec.brand and rec.model) or rec.brand.strip().lower() in GENERIC_BRANDS:
        return None
    row = conn.execute(
        """SELECT id FROM products
           WHERE category = %s AND lower(brand) = lower(%s) AND lower(model) = lower(%s)
           ORDER BY id LIMIT 1""",
        (category, rec.brand, rec.model),
    ).fetchone()
    return row["id"] if row else None


def _insert_product(conn: psycopg.Connection, rec: ProductRecord, category: str) -> int:
    return conn.execute(
        """INSERT INTO products (category, brand, model, name, attributes)
           VALUES (%s, %s, %s, %s, %s::jsonb) RETURNING id""",
        (category, rec.brand, rec.model, rec.title or rec.source_id, Jsonb(rec.attributes)),
    ).fetchone()["id"]


def log_fetch(
    conn: psycopg.Connection, source: str, url: str, status: str,
    http_status: int | None, duration_ms: int | None, note: str = "",
) -> None:
    conn.execute(
        """INSERT INTO fetch_log (source, url, status, http_status, duration_ms, note)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (source, url, status, http_status, duration_ms, note),
    )
    conn.commit()


def stored_attributes(conn: psycopg.Connection, source: str, source_id: str) -> dict:
    """Attributes already stored for this listing's product, so they are not extracted again."""
    row = conn.execute(
        """SELECT p.attributes FROM listings l JOIN products p ON p.id = l.product_id
           WHERE l.source = %s AND l.source_id = %s""",
        (source, source_id),
    ).fetchone()
    return dict(row["attributes"]) if row else {}


def known_source_ids(conn: psycopg.Connection, source: str, source_ids: list[str]) -> set[str]:
    rows = conn.execute("SELECT source_id FROM listings WHERE source = %s AND source_id = ANY(%s)",
                        (source, source_ids)).fetchall()
    return {row["source_id"] for row in rows}


def refresh_listings(conn: psycopg.Connection, candidates: list[Candidate]) -> None:
    """Write the rating and the review count of fresh result cards to the stored listings."""
    for cand in candidates:
        if cand.rating is None and cand.review_count is None:
            continue
        conn.execute("UPDATE listings SET rating = COALESCE(%s, rating), review_count = COALESCE(%s, review_count) "
                     "WHERE source = %s AND source_id = %s",
                     (cand.rating, cand.review_count, cand.source, cand.source_id))
    conn.commit()


def add_eliminations(conn: psycopg.Connection, thread_id: str, product_ids: list[int], reason: str,
                     removed_by: str = "user") -> None:
    for product_id in product_ids:
        conn.execute("INSERT INTO eliminations (thread_id, product_id, reason, removed_by) VALUES (%s, %s, %s, %s)",
                     (thread_id, product_id, reason, removed_by))
    conn.commit()


def eliminated_products(conn: psycopg.Connection, thread_id: str) -> set[int]:
    rows = conn.execute("SELECT product_id FROM eliminations WHERE thread_id = %s", (thread_id,)).fetchall()
    return {row["product_id"] for row in rows}


def restore_products(conn: psycopg.Connection, thread_id: str, product_ids: list[int]) -> None:
    conn.execute("DELETE FROM eliminations WHERE thread_id = %s AND product_id = ANY(%s)", (thread_id, product_ids))
    conn.commit()


def chunks_to_embed(conn: psycopg.Connection, embed_model: str, limit: int = 256) -> list[dict]:
    """Chunks without a vector, or with a vector of another model."""
    return conn.execute(
        """SELECT id, text FROM chunks WHERE embedding IS NULL OR embed_model IS DISTINCT FROM %s
           ORDER BY id LIMIT %s""", (embed_model, limit)).fetchall()


def save_vectors(conn: psycopg.Connection, rows: list[tuple[int, list[float], dict, list[str]]],
                 embed_model: str) -> None:
    """rows: chunk id, dense vector, sparse weights, BM25 tokens."""
    for chunk_id, dense, sparse, tokens in rows:
        conn.execute(
            "UPDATE chunks SET embedding = %s::vector, sparse = %s, tokens = %s, embed_model = %s WHERE id = %s",
            (str(dense), Jsonb(sparse), tokens, embed_model, chunk_id),
        )
