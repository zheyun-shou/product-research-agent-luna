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
-- The GTIN (EAN) of a product page of another shop (read_page), 14 digits. It matches one product across shops.
ALTER TABLE products ADD COLUMN IF NOT EXISTS gtin text;
CREATE INDEX IF NOT EXISTS products_gtin ON products (gtin);
-- find_product compares the models without spaces and hyphens
CREATE INDEX IF NOT EXISTS products_brand_loose_model
    ON products (category, lower(brand), regexp_replace(lower(model), '[[:space:]-]', '', 'g'));

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

-- On-demand extraction (spec 4.4): which missing attributes of a product were looked for in its text, and when.
-- A change of that text makes a new search possible.
CREATE TABLE IF NOT EXISTS attribute_searches (
    product_id   bigint NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    attribute    text NOT NULL,
    searched_at  timestamptz NOT NULL,
    found        boolean NOT NULL,
    text_hash    text NOT NULL DEFAULT '',  -- md5 of the specs, description and image text that the search read
    PRIMARY KEY (product_id, attribute)
);
ALTER TABLE attribute_searches ADD COLUMN IF NOT EXISTS text_hash text NOT NULL DEFAULT '';

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

-- Web searches (phase 3 Task 9). results holds the found pages with their text, so the same question in the next
-- 7 days costs no Tavily credit. listing_ids are the products that the search was for (the floor of the picks).
CREATE TABLE IF NOT EXISTS web_searches (
    id           bigserial PRIMARY KEY,
    question     text NOT NULL,
    listing_ids  bigint[] NOT NULL DEFAULT '{}',
    results      jsonb NOT NULL DEFAULT '[]'::jsonb,
    searched_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS web_searches_question ON web_searches (lower(question), searched_at);

-- What the agent remembers about a shopper across conversations (phase 3 Task 13): likes, dislikes and facts such
-- as the size of the kitchen. The shopper sees and deletes them on the web page. category NULL: all categories.
CREATE TABLE IF NOT EXISTS shopper_memory (
    id          bigserial PRIMARY KEY,
    shopper     text NOT NULL,
    kind        text NOT NULL CHECK (kind IN ('like', 'dislike', 'fact')),
    text        text NOT NULL,
    category    text,
    thread_id   text NOT NULL DEFAULT '',
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS shopper_memory_shopper ON shopper_memory (shopper);
-- remember: the chat model kept what the shopper said; removed: the reason of a removed product, with its title
ALTER TABLE shopper_memory ADD COLUMN IF NOT EXISTS source text NOT NULL DEFAULT 'remember';
