"""Write product records and fetch logs to Postgres."""
from dataclasses import dataclass

import psycopg
from psycopg.types.json import Jsonb

from scout.sources.base import ProductRecord


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
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return SavedIds(product_id, listing_id, variant_id)


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
