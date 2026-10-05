"""Write product records and fetch logs to Postgres."""
from dataclasses import dataclass

import psycopg
from psycopg.types.json import Jsonb

from scout.sources.base import Candidate, ProductRecord


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
    conn.execute(
        """INSERT INTO page_texts (listing_id, fetched_at, specs, bullets, description, reviews, images)
           VALUES (%s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (listing_id) DO UPDATE SET fetched_at = EXCLUDED.fetched_at, specs = EXCLUDED.specs,
               bullets = EXCLUDED.bullets, description = EXCLUDED.description, reviews = EXCLUDED.reviews,
               images = EXCLUDED.images
           WHERE page_texts.fetched_at <= EXCLUDED.fetched_at""",
        (listing_id, rec.fetched_at, Jsonb(rec.specs), Jsonb(rec.bullets), rec.description, Jsonb(rec.reviews),
         Jsonb(rec.images)),
    )


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
