-- =============================================================================
-- Fraud protection upgrade: site-wide IP blocking, device pending-order lock
-- + cooldown, and finally wiring up the two dormant soft-flag rules
-- (hold_first_high_value / flag_burst_orders — see migrations/009, which
-- defined them in sites.fraud_rules but never enforced them anywhere).
--
-- Small-business tier, deliberately: no courier-network data, no ML risk
-- scoring — just IP/device signals a solo merchant's storefront can act on
-- with zero setup. See app/fraud.py and app/api/public.py's create_public_order.
--
-- CLAUDE.md rule 6: every FK gets an explicit index, comment names the query
-- it serves. CLAUDE.md rule 8: fraud_status/fraud_reason/device_id on orders
-- are review-workflow metadata, not order totals/line items — they don't
-- touch the immutable snapshot columns that rule protects.
-- =============================================================================

CREATE TABLE IF NOT EXISTS fraud_ip_blocklist (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    site_id     uuid        NOT NULL REFERENCES sites(id)   ON DELETE CASCADE,
    ip_address  inet        NOT NULL,
    note        text        NOT NULL DEFAULT '',
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_fraud_ip_blocklist_site_ip UNIQUE (site_id, ip_address)
);

-- INDEX: crud.get_scoped/list_scoped tenant-scoped lookups (dashboard CRUD)
CREATE INDEX IF NOT EXISTS idx_fraud_ip_blocklist_tenant ON fraud_ip_blocklist (tenant_id);
-- INDEX: the IP-block middleware's per-site blocklist rebuild-on-cache-miss query
CREATE INDEX IF NOT EXISTS idx_fraud_ip_blocklist_site ON fraud_ip_blocklist (site_id);

ALTER TABLE orders ADD COLUMN IF NOT EXISTS device_id text;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS fraud_status text NOT NULL DEFAULT 'clear';
ALTER TABLE orders ADD COLUMN IF NOT EXISTS fraud_reason text;

-- INDEX: device pending-lock ("open order for this device?") and cooldown
-- ("N recent cancelled/fraud orders for this device?") lookups. Partial:
-- most orders (POS, orders placed before this shipped) have no device_id.
CREATE INDEX IF NOT EXISTS idx_orders_site_device
    ON orders (site_id, device_id, created_at DESC) WHERE device_id IS NOT NULL;

-- INDEX: Suspicious Orders review list — one site, flagged-only, newest first
CREATE INDEX IF NOT EXISTS idx_orders_site_fraud_flagged
    ON orders (site_id, created_at DESC) WHERE fraud_status = 'flagged';

-- Not the real access-control layer (backend connects as postgres and
-- bypasses RLS) — closes the Supabase auto-REST/PostgREST exposure gap for
-- anon/authenticated roles, same as every other tenant-owned table.
ALTER TABLE fraud_ip_blocklist ENABLE ROW LEVEL SECURITY;
