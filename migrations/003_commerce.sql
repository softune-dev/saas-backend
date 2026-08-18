-- =============================================================================
--  003_commerce.sql — categories, products, orders, order_items
-- =============================================================================
--  Run AFTER 002_sites.sql.
--
--  Scoping decision: these tables carry BOTH site_id and tenant_id.
--    site_id   — because one tenant may own several sites, each its own shop
--                with its own catalogue. Products belong to a storefront.
--    tenant_id — the isolation filter, denormalised for the same reasons as in
--                site_pages: no JOIN needed to prove ownership.
-- =============================================================================


-- =============================================================================
--  CATEGORIES — self-referencing tree
-- =============================================================================
CREATE TABLE IF NOT EXISTS categories (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    site_id     uuid        NOT NULL REFERENCES sites(id)   ON DELETE CASCADE,

    -- Self FK for subcategories. ON DELETE SET NULL, not CASCADE: deleting
    -- "Drinks" should orphan "Coffee" up to the root, NOT silently delete the
    -- customer's products underneath it. Destructive cascades on customer data
    -- are how you get a support ticket you cannot undo.
    parent_id   uuid        REFERENCES categories(id) ON DELETE SET NULL,

    name        text        NOT NULL,
    slug        citext      NOT NULL,
    description text,
    image_url   text,
    sort_order  integer     NOT NULL DEFAULT 0,
    is_active   boolean     NOT NULL DEFAULT true,

    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT uq_categories_site_slug UNIQUE (site_id, slug)
);

-- INDEX: the category menu for a storefront, in display order, active only.
CREATE INDEX IF NOT EXISTS idx_categories_site_order
    ON categories (site_id, sort_order, name)
    WHERE is_active;

-- INDEX: children of a node. Partial because most categories are top-level
-- (parent_id IS NULL) and those rows would just be dead weight in this index.
CREATE INDEX IF NOT EXISTS idx_categories_parent
    ON categories (parent_id)
    WHERE parent_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_categories_tenant ON categories (tenant_id);

DROP TRIGGER IF EXISTS trg_categories_updated_at ON categories;
CREATE TRIGGER trg_categories_updated_at
    BEFORE UPDATE ON categories
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();


-- =============================================================================
--  PRODUCTS
-- =============================================================================
CREATE TABLE IF NOT EXISTS products (
    id            uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     uuid        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    site_id       uuid        NOT NULL REFERENCES sites(id)   ON DELETE CASCADE,

    -- SET NULL: deleting a category must not delete the products in it.
    category_id   uuid        REFERENCES categories(id) ON DELETE SET NULL,

    sku           citext,
    name          text        NOT NULL,
    slug          citext      NOT NULL,
    description   text,

    -- MONEY AS INTEGER CENTS. Never float/double for money — 0.1 + 0.2 is not
    -- 0.3 in binary floating point, and those fractions of a cent accumulate
    -- into real accounting errors. Integer cents makes every arithmetic
    -- operation exact. NUMERIC(12,2) is the other correct choice; cents is
    -- chosen here because it maps cleanly to JSON (no string-vs-number issues
    -- when it crosses into JavaScript) and to payment APIs like Stripe, which
    -- also take integer minor units.
    price_cents   integer     NOT NULL DEFAULT 0 CHECK (price_cents >= 0),

    -- Optional "was" price for showing a discount. Must exceed price when set.
    compare_at_cents integer  CHECK (compare_at_cents IS NULL OR compare_at_cents >= price_cents),

    currency      char(3)     NOT NULL DEFAULT 'USD',

    stock         integer     NOT NULL DEFAULT 0,

    -- When true, the product sells regardless of stock (services, digital goods).
    track_stock   boolean     NOT NULL DEFAULT true,

    -- Array of { url, alt, sort } — variable length, so JSONB rather than five
    -- nullable image_1_url…image_5_url columns.
    images        jsonb       NOT NULL DEFAULT '[]'::jsonb,

    -- Per-product custom fields that differ wildly by industry (size, ABV,
    -- material, prep time). Exactly the case JSONB exists for.
    attributes    jsonb       NOT NULL DEFAULT '{}'::jsonb,

    is_active     boolean     NOT NULL DEFAULT true,

    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT uq_products_site_slug UNIQUE (site_id, slug)
);

-- UNIQUE INDEX: SKU is unique within a storefront, but optional.
-- Partial so the many NULL-SKU rows are excluded from the index entirely.
CREATE UNIQUE INDEX IF NOT EXISTS uq_products_site_sku
    ON products (site_id, sku)
    WHERE sku IS NOT NULL;

-- INDEX: the storefront product grid — active products of a site, newest first.
-- Equality column (site_id) first, sort column (created_at DESC) second.
-- INCLUDE carries the card-display fields so listing 24 products is one
-- index-only scan instead of 24 random heap reads.
CREATE INDEX IF NOT EXISTS idx_products_site_active_created
    ON products (site_id, created_at DESC)
    INCLUDE (name, slug, price_cents, currency, images)
    WHERE is_active;

-- INDEX: browsing one category, in price order (the common "sort by price" UI).
CREATE INDEX IF NOT EXISTS idx_products_category_price
    ON products (category_id, price_cents)
    WHERE is_active AND category_id IS NOT NULL;

-- INDEX: product search, e.g. name ILIKE '%coff%'.
-- WHY GIN + trigram: a normal b-tree index is useless for a LEADING wildcard,
-- so without this every search is a full table scan. Trigrams break the text
-- into 3-char chunks and index those, making substring search index-backed.
-- Chosen over a tsvector/full-text index because customers type partial words
-- and typos ("cofee") — trigram similarity handles that; full-text does not.
CREATE INDEX IF NOT EXISTS idx_products_name_trgm
    ON products USING gin (name gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_products_tenant ON products (tenant_id);

-- INDEX: low-stock alerts / restock dashboards.
-- Partial on a narrow condition means this index stays tiny no matter how big
-- the catalogue grows, because only genuinely low rows qualify.
CREATE INDEX IF NOT EXISTS idx_products_low_stock
    ON products (site_id, stock)
    WHERE track_stock AND is_active AND stock <= 5;

DROP TRIGGER IF EXISTS trg_products_updated_at ON products;
CREATE TRIGGER trg_products_updated_at
    BEFORE UPDATE ON products
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();


-- =============================================================================
--  ORDERS
-- =============================================================================
CREATE TABLE IF NOT EXISTS orders (
    id             uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id      uuid        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    site_id        uuid        NOT NULL REFERENCES sites(id)   ON DELETE CASCADE,

    -- Human-facing reference the customer quotes in support emails ("ORD-1042").
    -- Generated in app/crud.py, unique per site.
    order_number   text        NOT NULL,

    -- Buyer details as JSONB, NOT a foreign key to users.
    -- WHY: these are the customer's customers — they check out as guests and
    -- never log into your admin panel. Giving them rows in `users` would mix two
    -- completely different populations in one table and complicate every auth
    -- query. Shape: { name, email, phone, address: {...} }
    customer       jsonb       NOT NULL DEFAULT '{}'::jsonb,

    status         text        NOT NULL DEFAULT 'pending'
                               CHECK (status IN ('pending', 'paid', 'fulfilled',
                                                 'cancelled', 'refunded')),

    -- Totals stored, not computed on read. Deliberate denormalisation: an order
    -- is a historical record. If a product's price changes next week, last
    -- month's order total must NOT change with it. Recomputing from current
    -- product prices would silently rewrite history.
    subtotal_cents integer     NOT NULL DEFAULT 0 CHECK (subtotal_cents >= 0),
    shipping_cents integer     NOT NULL DEFAULT 0 CHECK (shipping_cents >= 0),
    tax_cents      integer     NOT NULL DEFAULT 0 CHECK (tax_cents      >= 0),
    total_cents    integer     NOT NULL DEFAULT 0 CHECK (total_cents    >= 0),
    currency       char(3)     NOT NULL DEFAULT 'USD',

    notes          text,

    -- Which funnel/campaign produced this order. Empty until you build funnels.
    meta           jsonb       NOT NULL DEFAULT '{}'::jsonb,

    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT uq_orders_site_number UNIQUE (site_id, order_number)
);

-- INDEX: the orders dashboard — one site, newest first. The single most-run
-- query in any admin panel.
CREATE INDEX IF NOT EXISTS idx_orders_site_created
    ON orders (site_id, created_at DESC)
    INCLUDE (order_number, status, total_cents, currency);

-- INDEX: filtering the dashboard by status ("show me unfulfilled orders").
-- Three columns, in the order the query uses them: equality, equality, sort.
CREATE INDEX IF NOT EXISTS idx_orders_site_status_created
    ON orders (site_id, status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_orders_tenant ON orders (tenant_id);

-- INDEX: "find my order" by customer email, extracted from the JSONB.
-- An EXPRESSION index: it indexes the result of the ->> operator, so a lookup
-- on a nested JSON field is as fast as one on a real column. This is the trick
-- that makes JSONB storage practical rather than a performance trap.
CREATE INDEX IF NOT EXISTS idx_orders_customer_email
    ON orders ((customer ->> 'email'))
    WHERE customer ->> 'email' IS NOT NULL;

DROP TRIGGER IF EXISTS trg_orders_updated_at ON orders;
CREATE TRIGGER trg_orders_updated_at
    BEFORE UPDATE ON orders
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();


-- =============================================================================
--  ORDER_ITEMS — line items
-- =============================================================================
CREATE TABLE IF NOT EXISTS order_items (
    id               uuid        PRIMARY KEY DEFAULT gen_random_uuid(),

    order_id         uuid        NOT NULL REFERENCES orders(id) ON DELETE CASCADE,

    -- SET NULL, not CASCADE or RESTRICT: if a merchant deletes a product, past
    -- orders must survive intact. The snapshot columns below are why that works.
    product_id       uuid        REFERENCES products(id) ON DELETE SET NULL,

    -- SNAPSHOTS — the name, SKU and price AS THEY WERE at purchase time.
    -- This is the whole reason a deleted product doesn't corrupt an old order,
    -- and why an invoice reprinted a year later still shows what was actually
    -- bought and paid. Never render a historical order by joining to the live
    -- products table.
    name_snapshot    text        NOT NULL,
    sku_snapshot     text,
    unit_price_cents integer     NOT NULL CHECK (unit_price_cents >= 0),

    quantity         integer     NOT NULL CHECK (quantity > 0),

    -- Stored rather than a generated column so a manual discount on one line is
    -- possible later without fighting the constraint.
    total_cents      integer     NOT NULL CHECK (total_cents >= 0),

    created_at       timestamptz NOT NULL DEFAULT now()
);

-- INDEX: fetch the lines of an order — runs on every order-detail view.
CREATE INDEX IF NOT EXISTS idx_order_items_order ON order_items (order_id);

-- INDEX: "how many times has this product sold?" (best-sellers reporting).
CREATE INDEX IF NOT EXISTS idx_order_items_product
    ON order_items (product_id)
    WHERE product_id IS NOT NULL;
