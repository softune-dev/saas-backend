-- =============================================================================
-- 088_billing_periods.sql: paying for 1, 3, 6 or 12 months, and lapsing to Free.
--
--   A. payment_claims.months, invoices.months: how many months a payment covers.
--   B. tenants.plan_months: the period the tenant last paid for, so a renewal
--      reminder can quote the same price. tenants.plan_lapsed_at: when a paid
--      plan dropped to Free (NULL = never, or paying again).
--   C. products.plan_hidden: kept but not shown on the store, because the tenant
--      lapsed to Free with more products than Free allows.
--   D. notifications.type may be 'plan_lapsed'.
--
-- SAFE TO RUN WHILE THE OLD CODE IS LIVE: additive only. Every new column has a
-- constant default or is nullable (no table rewrite in Postgres 11+), so the old
-- code's INSERTs keep working and every existing row means what it meant: one
-- month, not lapsed, not hidden. The CHECK in D is widened; the previous values
-- are copied verbatim from 087.
--
-- No new index: plan_hidden is only read next to site_id, which is indexed, and
-- tenants is small (the lapse sweep reads it whole, like the sweeps before it).
--
-- Idempotent. Separate short transactions, each with its own lock_timeout: a
-- failed part changes nothing, run the whole file again.
-- =============================================================================

-- A. months on claims and invoices ------------------------------------------------
BEGIN;
SET LOCAL lock_timeout = '5s';
ALTER TABLE payment_claims ADD COLUMN IF NOT EXISTS months smallint NOT NULL DEFAULT 1;
ALTER TABLE payment_claims DROP CONSTRAINT IF EXISTS payment_claims_months_check;
ALTER TABLE payment_claims ADD CONSTRAINT payment_claims_months_check CHECK (months IN (1, 3, 6, 12));
COMMIT;

BEGIN;
SET LOCAL lock_timeout = '5s';
ALTER TABLE invoices ADD COLUMN IF NOT EXISTS months smallint NOT NULL DEFAULT 1;
ALTER TABLE invoices DROP CONSTRAINT IF EXISTS invoices_months_check;
ALTER TABLE invoices ADD CONSTRAINT invoices_months_check CHECK (months IN (1, 3, 6, 12));
COMMIT;

-- B. tenants ----------------------------------------------------------------------
BEGIN;
SET LOCAL lock_timeout = '5s';
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS plan_months smallint NOT NULL DEFAULT 1;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS plan_lapsed_at timestamptz;
ALTER TABLE tenants DROP CONSTRAINT IF EXISTS tenants_plan_months_check;
ALTER TABLE tenants ADD CONSTRAINT tenants_plan_months_check CHECK (plan_months IN (1, 3, 6, 12));
COMMIT;

-- C. products ---------------------------------------------------------------------
BEGIN;
SET LOCAL lock_timeout = '5s';
ALTER TABLE products ADD COLUMN IF NOT EXISTS plan_hidden boolean NOT NULL DEFAULT false;
COMMIT;

-- D. notification types -------------------------------------------------------------
BEGIN;
SET LOCAL lock_timeout = '5s';
ALTER TABLE notifications DROP CONSTRAINT IF EXISTS notifications_type_check;
ALTER TABLE notifications ADD CONSTRAINT notifications_type_check
    CHECK (type IN (
        'order_created', 'order_blocked', 'site_published', 'site_unpublished',
        'payment_verified', 'credit_purchase_verified',
        'order_flagged', 'order_limit', 'plan_lapsed'
    ));
COMMIT;

-- Check afterwards (expect 5 rows):
--   SELECT table_name, column_name, column_default FROM information_schema.columns
--    WHERE (table_name, column_name) IN (('payment_claims','months'), ('invoices','months'),
--          ('tenants','plan_months'), ('tenants','plan_lapsed_at'), ('products','plan_hidden'));
