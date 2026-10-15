"""Write product records and fetch logs to Postgres."""
import re
from dataclasses import dataclass

import psycopg
from psycopg.types.json import Jsonb

from scout.sources.base import Candidate, FilterGroup, ProductRecord
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
                       gtin = COALESCE(gtin, %s), attributes = attributes || %s::jsonb
                   WHERE id = %s""",
                (rec.brand, rec.model, rec.gtin, Jsonb(rec.attributes), product_id),
            )
            conn.execute(
                """UPDATE listings SET url = %s, title = COALESCE(%s, title), rating = COALESCE(%s, rating),
                       review_count = COALESCE(%s, review_count), fetched_at = %s
                   WHERE id = %s""",
                (rec.url, rec.title, rec.rating, rec.review_count, rec.fetched_at, listing_id),
            )
        else:
            product_id = find_product(conn, rec, category) or _insert_product(conn, rec, category)
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


# A hash of the product text that on-demand extraction reads. A recheck of the same page gives the same hash, and
# new review chunks do not change it. The kinds are those of scout.extract.ondemand.KINDS.
_TEXT_HASH = """
SELECT COALESCE(md5(string_agg(c.text, '|' ORDER BY c.listing_id, c.ref)), '') FROM chunks c
JOIN listings l ON l.id = c.listing_id
WHERE l.product_id = {product} AND c.kind IN ('specs', 'description', 'image_text')"""


def mark_attribute_search(conn: psycopg.Connection, product_id: int, name: str, found: bool) -> None:
    conn.execute(
        f"""INSERT INTO attribute_searches (product_id, attribute, searched_at, found, text_hash)
            VALUES (%(p)s, %(a)s, now(), %(f)s, ({_TEXT_HASH.format(product="%(p)s")}))
            ON CONFLICT (product_id, attribute) DO UPDATE SET searched_at = now(), found = EXCLUDED.found,
                text_hash = EXCLUDED.text_hash""",
        {"p": product_id, "a": name, "f": found})
    conn.commit()


def attribute_searches(conn: psycopg.Connection, product_ids: list[int]) -> set[tuple[int, str]]:
    """The (product, attribute) pairs that were looked for in the text that the product has now."""
    rows = conn.execute(
        f"""SELECT a.product_id, a.attribute FROM attribute_searches a
            WHERE a.product_id = ANY(%s) AND a.text_hash = ({_TEXT_HASH.format(product="a.product_id")})""",
        (product_ids,)).fetchall()
    return {(row["product_id"], row["attribute"]) for row in rows}


def product_listing_ids(conn: psycopg.Connection, product_id: int) -> list[int]:
    return [row["id"] for row in conn.execute("SELECT id FROM listings WHERE product_id = %s ORDER BY id",
                                              (product_id,)).fetchall()]


def page_texts(conn: psycopg.Connection, listing_ids: list[int]) -> dict[int, dict]:
    rows = conn.execute("SELECT * FROM page_texts WHERE listing_id = ANY(%s)", (listing_ids,)).fetchall()
    return {row["listing_id"]: dict(row) for row in rows}


# Sellers without a brand use these names. Two listings with such a brand are not the same product.
GENERIC_BRANDS = {"generic", "generisk", "unbranded", "no brand", "okänt märke", "ingen märkning"}


def find_product(conn: psycopg.Connection, rec: ProductRecord, category: str) -> int | None:
    """The same product: the same GTIN, or the same brand and model. Spaces and hyphens in the model do not count,
    because shops write "RK4182PS4" and "RK 4182 PS4"."""
    if rec.gtin:
        row = conn.execute("SELECT id FROM products WHERE category = %s AND gtin = %s ORDER BY id LIMIT 1",
                           (category, rec.gtin)).fetchone()
        if row:
            return row["id"]
    if not (rec.brand and rec.model) or rec.brand.strip().lower() in GENERIC_BRANDS:
        return None
    row = conn.execute(
        """SELECT id FROM products
           WHERE category = %s AND lower(brand) = lower(%s)
             AND regexp_replace(lower(model), '[[:space:]-]', '', 'g')
                 = regexp_replace(lower(%s), '[[:space:]-]', '', 'g')
           ORDER BY id LIMIT 1""",
        (category, rec.brand, rec.model),
    ).fetchone()
    return row["id"] if row else None


def _insert_product(conn: psycopg.Connection, rec: ProductRecord, category: str) -> int:
    return conn.execute(
        """INSERT INTO products (category, brand, model, gtin, name, attributes)
           VALUES (%s, %s, %s, %s, %s, %s::jsonb) RETURNING id""",
        (category, rec.brand, rec.model, rec.gtin, rec.title or rec.source_id, Jsonb(rec.attributes)),
    ).fetchone()["id"]


def same_brand_listings(conn: psycopg.Connection, product_id: int, words: str, limit: int = 5) -> list[dict]:
    """Listings of other products with the same brand and category, from other sources, most words in common with
    `words` (the title and the model) first. Luna judges if one of them is the same product, because shops write
    the model in different ways (KG 320.2, or an article number)."""
    rows = conn.execute(
        """SELECT l.id, l.source, l.title, p.model FROM listings l JOIN products p ON p.id = l.product_id
           JOIN products me ON me.id = %(p)s
           WHERE p.id <> me.id AND p.category = me.category AND lower(p.brand) = lower(me.brand)
             AND l.source NOT IN (SELECT source FROM listings WHERE product_id = me.id)""",
        {"p": product_id}).fetchall()
    mine = set(re.findall(r"\w+", words.lower()))
    rows.sort(key=lambda r: (-len(mine & set(re.findall(r"\w+", f"{r['title']} {r['model']}".lower()))), r["id"]))
    return rows[:limit]


def join_products(conn: psycopg.Connection, listing_id: int, same_as: int) -> str | None:
    """Make the product of listing_id the same product as the one of same_as: its listings, removals, GTIN, model
    and attributes go to that product. The reviews of a moved shop page that the product has already are left out.
    Gives the problem, or None."""
    from scout.extract.verify import normalise
    rows = {r["id"]: r for r in conn.execute("SELECT id, product_id FROM listings WHERE id = ANY(%s)",
                                             ([listing_id, same_as],)).fetchall()}
    missing = [i for i in (listing_id, same_as) if i not in rows]
    if missing:
        return f"there is no product with id {missing[0]}"
    old, new = rows[listing_id]["product_id"], rows[same_as]["product_id"]
    if old == new:
        return None
    products = {r["id"]: r for r in conn.execute("SELECT id, category FROM products WHERE id = ANY(%s)",
                                                 ([old, new],)).fetchall()}
    if products[old]["category"] != products[new]["category"]:
        return "they are in different categories"
    shared = conn.execute(
        """SELECT a.source FROM listings a JOIN listings b ON a.source = b.source
           WHERE a.product_id = %s AND b.product_id = %s LIMIT 1""", (old, new)).fetchone()
    if shared:
        return f"both are listings of {shared['source']}; one shop does not list one product two times"
    conn.execute("UPDATE listings SET product_id = %s WHERE product_id = %s", (new, old))
    conn.execute("UPDATE eliminations SET product_id = %s WHERE product_id = %s", (new, old))
    conn.execute(
        """UPDATE products p SET gtin = COALESCE(p.gtin, o.gtin), model = COALESCE(p.model, o.model),
                  attributes = o.attributes || p.attributes
           FROM products o WHERE p.id = %s AND o.id = %s""", (new, old))
    conn.execute("DELETE FROM products WHERE id = %s", (old,))  # its attribute searches go with it
    # The page reviews that another listing of the product has: the newest page loses its copy, the review pages of
    # amazon.se (shop_reviews) keep theirs
    for row in conn.execute("""SELECT t.listing_id, t.reviews FROM page_texts t JOIN listings l ON l.id = t.listing_id
                               WHERE l.product_id = %s ORDER BY t.listing_id DESC""", (new,)).fetchall():
        if row["reviews"]:
            known = product_review_texts(conn, new, but=row["listing_id"])
            kept = [r for r in row["reviews"] if normalise(r.get("text") or "") not in known]
            if len(kept) < len(row["reviews"]):
                conn.execute("UPDATE page_texts SET reviews = %s WHERE listing_id = %s",
                             (Jsonb(kept), row["listing_id"]))
    return None


def product_review_texts(conn: psycopg.Connection, product_id: int, but: int | None = None) -> set[str]:
    """The normalised texts of the stored reviews of a product, in all its listings but one. Brands copy the same
    reviews to several shops (design section 1a)."""
    from scout.extract.verify import normalise
    rows = conn.execute(
        """SELECT r.text FROM shop_reviews r JOIN listings l ON l.id = r.listing_id
           WHERE l.product_id = %(p)s AND l.id IS DISTINCT FROM %(but)s
           UNION ALL
           SELECT e->>'text' FROM page_texts t JOIN listings l ON l.id = t.listing_id,
                  jsonb_array_elements(t.reviews) e
           WHERE l.product_id = %(p)s AND l.id IS DISTINCT FROM %(but)s""",
        {"p": product_id, "but": but}).fetchall()
    return {normalise(r["text"] or "") for r in rows} - {""}


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


# ---- web search (phase 3 Task 9) ----

def stored_web_results(conn: psycopg.Connection, question: str, since) -> list[dict] | None:
    """The results of the newest search with this question since the given time, or None."""
    row = conn.execute(
        """SELECT results FROM web_searches WHERE lower(question) = lower(%s) AND searched_at >= %s
           ORDER BY searched_at DESC LIMIT 1""", (question.strip(), since)).fetchone()
    return None if row is None else row["results"]


def save_web_search(conn: psycopg.Connection, question: str, listing_ids: list[int], results: list[dict],
                    since) -> None:
    """Add a search, or update the newest one with this question since the given time. Stored results keep their
    first date, so they are not used after 7 days; the products of the new use are added to them."""
    row = conn.execute(
        """SELECT id FROM web_searches WHERE lower(question) = lower(%s) AND searched_at >= %s
           ORDER BY searched_at DESC LIMIT 1""", (question.strip(), since)).fetchone()
    if row is None:
        conn.execute("INSERT INTO web_searches (question, listing_ids, results) VALUES (%s, %s, %s)",
                     (question.strip(), listing_ids, Jsonb(results)))
        return
    conn.execute(
        """UPDATE web_searches SET results = %s,
               listing_ids = ARRAY(SELECT DISTINCT unnest(listing_ids || %s::bigint[]))
           WHERE id = %s""", (Jsonb(results), listing_ids, row["id"]))


def save_web_chunks(conn: psycopg.Connection, listing_id: int, ref_prefix: str, chunks: list[Chunk],
                    fetched_at) -> None:
    """Replace the chunks of one web page for one listing. The same text stays, with its embeddings."""
    old = conn.execute("SELECT text FROM chunks WHERE listing_id = %s AND source = 'web' AND ref LIKE %s",
                       (listing_id, ref_prefix + " %")).fetchall()
    if sorted(r["text"] for r in old) == sorted(c.text for c in chunks):
        return
    conn.execute("DELETE FROM chunks WHERE listing_id = %s AND source = 'web' AND ref LIKE %s",
                 (listing_id, ref_prefix + " %"))
    for c in chunks:
        conn.execute(
            """INSERT INTO chunks (listing_id, source, kind, ref, text, meta, fetched_at)
               VALUES (%s, 'web', %s, %s, %s, %s, %s)""",
            (listing_id, c.kind, c.ref, c.text, Jsonb(c.meta), fetched_at),
        )


def web_searched(conn: psycopg.Connection, listing_ids: list[int], since) -> set[int]:
    """The listings whose product (any of its listings) had a web search since the given time."""
    rows = conn.execute(
        """SELECT l.id FROM listings l
           WHERE l.id = ANY(%s) AND EXISTS (
               SELECT 1 FROM web_searches w JOIN listings o ON o.id = ANY(w.listing_ids)
               WHERE o.product_id = l.product_id AND w.searched_at >= %s)""", (listing_ids, since)).fetchall()
    return {row["id"] for row in rows}


def add_memory(conn: psycopg.Connection, shopper: str, kind: str, text: str, category: str | None,
               thread_id: str = "", source: str = "remember") -> int:
    """Store one memory of the shopper and give its id. The same text of the same kind is stored one time; it gets
    the new category, so a memory can become one for all categories."""
    text = " ".join(text.split())
    row = conn.execute("UPDATE shopper_memory SET category = %s WHERE shopper = %s AND kind = %s"
                       " AND lower(text) = lower(%s) RETURNING id", (category, shopper, kind, text)).fetchone()
    if row is None:
        row = conn.execute("INSERT INTO shopper_memory (shopper, kind, text, category, thread_id, source)"
                           " VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                           (shopper, kind, text, category, thread_id, source)).fetchone()
    conn.commit()
    return row["id"]


def memories(conn: psycopg.Connection, shopper: str) -> list[dict]:
    """The memories of the shopper, oldest first: id, kind, text, category, source, created_at."""
    return conn.execute("SELECT id, kind, text, category, source, created_at FROM shopper_memory WHERE shopper = %s"
                        " ORDER BY id", (shopper,)).fetchall()


def forget_memories(conn: psycopg.Connection, shopper: str, ids: list[int]) -> list[int]:
    """Delete memories of the shopper and give the ids that were deleted."""
    rows = conn.execute("DELETE FROM shopper_memory WHERE shopper = %s AND id = ANY(%s) RETURNING id",
                        (shopper, ids)).fetchall()
    conn.commit()
    return sorted(r["id"] for r in rows)


def save_filters(conn: psycopg.Connection, source: str, category: str, groups: list[FilterGroup]) -> None:
    """Merge the filter groups of one search page into the stored ones. An option of the page replaces the stored
    option with the same value; the other stored options stay."""
    for group in groups:
        row = conn.execute("SELECT options FROM site_filters WHERE source = %s AND category = %s AND key = %s",
                           (source, category, group.key)).fetchone()
        options = {o["value"]: o for o in (row["options"] if row else [])}
        options.update({o.value: o.model_dump() for o in group.options})
        conn.execute(
            """INSERT INTO site_filters (source, category, key, name, options) VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (source, category, key) DO UPDATE
               SET name = EXCLUDED.name, options = EXCLUDED.options, updated_at = now()""",
            (source, category, group.key, group.name, Jsonb(list(options.values()))))
    conn.commit()


def load_filters(conn: psycopg.Connection, source: str, category: str) -> list[FilterGroup]:
    rows = conn.execute("SELECT key, name, options FROM site_filters WHERE source = %s AND category = %s ORDER BY key",
                        (source, category)).fetchall()
    return [FilterGroup(key=r["key"], name=r["name"], options=r["options"]) for r in rows]
