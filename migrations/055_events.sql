-- =============================================================================
--  055_events.sql — merchant sale/promo campaigns ("Events")
-- =============================================================================
--  An Event is a named discount campaign (e.g. "Summer Sale") a merchant
--  applies to any number of their own products. A product may belong to at
--  most one ACTIVE event at a time — enforced at the application layer
--  (app/events.py / app/api/events.py), not here, because draft/inactive
--  events must be free to list any products without conflict.
--
--  See CLAUDE.md rule 7 (integer cents — discount_percent is applied via
--  pure-integer round-half-up math in app/events.py, never here or in
--  floating point) and rule 8 (immutable order history — order_items gets
--  plain snapshot columns below, never a live FK to events, so a receipt
--  keeps showing the right discount even after the event is edited or
--  deleted).
-- =============================================================================

CREATE TABLE IF NOT EXISTS events (
    id                uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id         uuid        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    site_id           uuid        NOT NULL REFERENCES sites(id)   ON DELETE CASCADE,

    name              text        NOT NULL,
    slug              citext      NOT NULL,
    description       text,
    image_url         text,
    cta_label         text        NOT NULL DEFAULT 'Shop now',
    discount_percent  smallint    NOT NULL CHECK (discount_percent BETWEEN 1 AND 90),
    is_active         boolean     NOT NULL DEFAULT false,

    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT uq_events_site_slug UNIQUE (site_id, slug)
);

-- Pure association — membership itself is the only fact this table records.
CREATE TABLE IF NOT EXISTS event_products (
    event_id   uuid NOT NULL REFERENCES events(id)   ON DELETE CASCADE,
    product_id uuid NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    PRIMARY KEY (event_id, product_id)
);

-- INDEX: dashboard Events list — one site, newest first (mirrors categories'
-- idx_categories_site_order).
CREATE INDEX IF NOT EXISTS idx_events_site_created
    ON events (site_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_events_tenant ON events (tenant_id);

-- INDEX: checkout and public product serving only ever care about ACTIVE
-- events for a site — partial index keeps it small and hot.
CREATE INDEX IF NOT EXISTS idx_events_site_active
    ON events (site_id)
    WHERE is_active;

-- INDEX: "which active event (if any) is product X in" — checkout pricing
-- and public product listing both do this lookup per product. The
-- event_id -> product_id direction is already covered by the PK's
-- leftmost column, so only this reverse direction needs its own index.
CREATE INDEX IF NOT EXISTS idx_event_products_product
    ON event_products (product_id);

DROP TRIGGER IF EXISTS trg_events_updated_at ON events;
CREATE TRIGGER trg_events_updated_at
    BEFORE UPDATE ON events
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- Order receipts must permanently show which event/discount applied, even
-- after the event or the product itself is later edited or deleted — same
-- immutable-snapshot pattern as order_items.name_snapshot/sku_snapshot.
-- Nullable: most order items were never part of an event.
ALTER TABLE order_items ADD COLUMN IF NOT EXISTS event_name_snapshot text;
ALTER TABLE order_items ADD COLUMN IF NOT EXISTS event_discount_percent_snapshot smallint;

-- Close the Supabase auto-REST exposure gap — see migrations/046 and
-- migrations/054's own comment for why every new table needs this.
ALTER TABLE events ENABLE ROW LEVEL SECURITY;
ALTER TABLE event_products ENABLE ROW LEVEL SECURITY;
