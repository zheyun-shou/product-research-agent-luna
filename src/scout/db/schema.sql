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
