CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS products (
    id          bigserial PRIMARY KEY,
    category    text NOT NULL,
    brand       text,
    model       text,
    name        text NOT NULL,
    attributes  jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS products_brand_model
    ON products (category, lower(brand), lower(model));

CREATE TABLE IF NOT EXISTS listings (
    id            bigserial PRIMARY KEY,
    product_id    bigint NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    source        text NOT NULL,
    source_id     text NOT NULL,
    url           text NOT NULL,
    title         text,
    rating        numeric(3,2),
    review_count  integer,
    fetched_at    timestamptz NOT NULL,
    UNIQUE (source, source_id)
);

CREATE TABLE IF NOT EXISTS variants (
    id           bigserial PRIMARY KEY,
    listing_id   bigint NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    variant_key  text NOT NULL,
    label        text NOT NULL DEFAULT '',
    price        numeric(12,2),
    currency     text,
    in_stock     boolean,
    updated_at   timestamptz NOT NULL,
    UNIQUE (listing_id, variant_key)
);

CREATE TABLE IF NOT EXISTS price_history (
    id          bigserial PRIMARY KEY,
    variant_id  bigint NOT NULL REFERENCES variants(id) ON DELETE CASCADE,
    price       numeric(12,2),
    currency    text,
    in_stock    boolean,
    fetched_at  timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS fetch_log (
    id           bigserial PRIMARY KEY,
    source       text NOT NULL,
    url          text NOT NULL,
    fetched_at   timestamptz NOT NULL DEFAULT now(),
    status       text NOT NULL,
    http_status  integer,
    duration_ms  integer,
    note         text NOT NULL DEFAULT ''
);


CREATE TABLE IF NOT EXISTS eliminations (
    id          bigserial PRIMARY KEY,
    thread_id   text NOT NULL,
    product_id  bigint NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    reason      text NOT NULL DEFAULT '',
    removed_by  text NOT NULL CHECK (removed_by IN ('user', 'agent')),
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS eliminations_thread ON eliminations (thread_id);

-- The text of the newest product page of each listing: what the chat model reads with read_product.
CREATE TABLE IF NOT EXISTS page_texts (
    listing_id   bigint PRIMARY KEY REFERENCES listings(id) ON DELETE CASCADE,
    fetched_at   timestamptz NOT NULL,
    specs        jsonb NOT NULL DEFAULT '{}'::jsonb,
    bullets      jsonb NOT NULL DEFAULT '[]'::jsonb,
    description  text NOT NULL DEFAULT '',
    reviews      jsonb NOT NULL DEFAULT '[]'::jsonb,
    images       jsonb NOT NULL DEFAULT '[]'::jsonb
);

-- Reviews from the review pages of a listing (phase 3 Task 5), one row for each review. filter is the star filter
-- that found it: positive (4 and 5 stars) or critical (1 to 3 stars). The author is never stored.
CREATE TABLE IF NOT EXISTS shop_reviews (
    listing_id   bigint NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    review_id    text NOT NULL,
    filter       text NOT NULL CHECK (filter IN ('positive', 'critical')),
    title        text NOT NULL DEFAULT '',
    rating       numeric(2, 1),
    date         text NOT NULL DEFAULT '',
    verified     boolean NOT NULL DEFAULT false,
    variant      text NOT NULL DEFAULT '',
    text         text NOT NULL,
    fetched_at   timestamptz NOT NULL,
    PRIMARY KEY (listing_id, review_id)
);

-- The last read of the review pages of each listing, also when it found no review. complete is false when a
-- skipped login or the page limit stopped the read, so the next search reads the listing again.
CREATE TABLE IF NOT EXISTS review_reads (
    listing_id   bigint PRIMARY KEY REFERENCES listings(id) ON DELETE CASCADE,
    read_at      timestamptz NOT NULL,
    complete     boolean NOT NULL
);

-- Parts of the page texts for retrieval (spec 4.4 and 4.6). source is the page kind that the text came from.
CREATE TABLE IF NOT EXISTS chunks (
    id           bigserial PRIMARY KEY,
    listing_id   bigint NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    source       text NOT NULL DEFAULT 'product_page',
    kind         text NOT NULL,
    ref          text NOT NULL,
    text         text NOT NULL,
    meta         jsonb NOT NULL DEFAULT '{}'::jsonb,
    fetched_at   timestamptz NOT NULL,
    tokens       text[],
    embedding    vector(1024),
    sparse       jsonb,
    embed_model  text,
    UNIQUE (listing_id, source, ref)
);
CREATE INDEX IF NOT EXISTS chunks_listing ON chunks (listing_id);
CREATE INDEX IF NOT EXISTS chunks_embedding ON chunks USING hnsw (embedding vector_cosine_ops);
