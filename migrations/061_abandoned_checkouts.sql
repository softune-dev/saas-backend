-- Abandoned checkout capture: records a shopper's phone number + cart as
-- soon as they enter a valid phone during checkout, before they submit the
-- order. Lets a merchant see (and manually follow up with) shoppers who
-- started checkout but never completed it — no automated messaging yet,
-- this is data capture only. See app/api/public.py's capture_abandoned_checkout.

CREATE TABLE IF NOT EXISTS abandoned_checkouts (
    id             uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id      uuid        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    site_id        uuid        NOT NULL REFERENCES sites(id)   ON DELETE CASCADE,
    phone          text        NOT NULL,
    -- [{product_id, quantity}, ...] — resolved against live products for
    -- display; deliberately not a name/price snapshot since this is
    -- ephemeral "who almost bought" data, not immutable order history
    -- (contrast with CLAUDE.md rule 8's order_items snapshot columns).
    items          jsonb       NOT NULL DEFAULT '[]'::jsonb,
    subtotal_cents integer     NOT NULL DEFAULT 0,
    -- Set once a real order from this site+phone completes, so the
    -- dashboard list can exclude shoppers who actually converted.
    converted_at   timestamptz,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    -- One active row per phone per site — re-entering/updating the phone
    -- field during the same or a later visit upserts this row rather than
    -- piling up duplicates for the same shopper.
    CONSTRAINT uq_abandoned_checkouts_site_phone UNIQUE (site_id, phone)
);

-- INDEX: dashboard list — one site's abandoned checkouts, most recently
-- seen first. updated_at doubles as "last seen" since every capture call
-- upserts and bumps it.
CREATE INDEX IF NOT EXISTS idx_abandoned_checkouts_site_updated
    ON abandoned_checkouts (site_id, updated_at DESC);

-- INDEX: tenant-wide isolation checks (crud.get_scoped/list_scoped).
CREATE INDEX IF NOT EXISTS idx_abandoned_checkouts_tenant
    ON abandoned_checkouts (tenant_id);

DROP TRIGGER IF EXISTS trg_abandoned_checkouts_updated_at ON abandoned_checkouts;
CREATE TRIGGER trg_abandoned_checkouts_updated_at
    BEFORE UPDATE ON abandoned_checkouts
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
