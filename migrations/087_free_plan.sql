-- =============================================================================
-- 087_free_plan.sql: groundwork for the Free plan.
--
--   A. notifications.type may be 'order_limit' (limit reached) and
--      'order_flagged'. order_flagged is already published by app/api/public.py
--      for fraud-flagged orders but was never in the CHECK. After this the
--      DATABASE accepts it; the app still drops it (notifications.py) and the
--      dashboard still does not show it until the dashboard supports it.
--   B. marketing_contacts.lifecycle may be 'free'.
--   C. tenants.plan may be 'free'; tenants.last_active_at (when the merchant
--      last did something real, for a later abandoned-store sweep) is NOT NULL
--      DEFAULT now(), so every existing tenant's clock starts when this runs.
--   D. tenant_order_months: orders per tenant per calendar month, the counter a
--      free plan's monthly order limit is enforced against. `blocked` counts
--      orders refused after the limit was hit.
--
-- SAFE TO RUN WHILE THE OLD CODE IS LIVE: additive only. Every new column has a
-- default, so the old code's INSERTs keep working. Each CHECK is WIDENED: the
-- previous values are copied verbatim, so no existing row or write can fail it.
--   notifications_type_check      previous: 069 (order_created, order_blocked,
--                                 site_published, site_unpublished,
--                                 payment_verified, credit_purchase_verified)
--   marketing_contacts lifecycle  previous: 077 (lead, trial, customer, churned,
--                                 former_customer)
--   tenants_plan_check            previous: 049 (trial, demo, starter, growth,
--                                 business)
--
-- Four separate transactions, ordered so the busiest table (tenants) is locked
-- last and briefly, and an ACCESS EXCLUSIVE lock on tenants is never held while
-- notifications is scanned. Each part is idempotent and has its own
-- lock_timeout: it fails cleanly instead of queueing behind a long query and
-- stalling production. A FAILED PART IS FIXED BY RUNNING THE WHOLE FILE AGAIN;
-- parts that already succeeded are harmless to repeat. (If your SQL editor
-- wraps the whole file in one transaction, it still works, just in one piece.)
-- Re-adding a CHECK scans a small table under a brief lock; NOT VALID would not
-- shorten that, since the lock is held until COMMIT anyway.
-- =============================================================================

-- A. notification types ---------------------------------------------------------
BEGIN;
SET LOCAL lock_timeout = '5s';

ALTER TABLE notifications DROP CONSTRAINT IF EXISTS notifications_type_check;
ALTER TABLE notifications ADD CONSTRAINT notifications_type_check
    CHECK (type IN (
        'order_created', 'order_blocked', 'site_published', 'site_unpublished',
        'payment_verified', 'credit_purchase_verified',
        'order_flagged', 'order_limit'
    ));
COMMIT;

-- B. contact lifecycle ----------------------------------------------------------
BEGIN;
SET LOCAL lock_timeout = '5s';

-- The CHECK was declared inline in 077, so Postgres generated its name. Find it
-- by what it constrains: a CHECK whose only column (conkey) is `lifecycle`.
-- Matching on the column, not on the text of the definition, cannot catch an
-- unrelated CHECK that merely mentions the word. Re-runs also replace our own
-- named constraint, since it is a CHECK on the same column.
DO $$
DECLARE
    c record;
BEGIN
    FOR c IN
        SELECT con.conname
        FROM pg_constraint con
        JOIN pg_attribute att
          ON att.attrelid = con.conrelid
         AND att.attname = 'lifecycle'
         AND NOT att.attisdropped
        WHERE con.conrelid = 'marketing_contacts'::regclass
          AND con.contype = 'c'
          AND con.conkey = ARRAY[att.attnum]
    LOOP
        EXECUTE format('ALTER TABLE marketing_contacts DROP CONSTRAINT %I', c.conname);
    END LOOP;
END $$;

ALTER TABLE marketing_contacts ADD CONSTRAINT marketing_contacts_lifecycle_check
    CHECK (lifecycle IN ('lead', 'trial', 'free', 'customer', 'churned', 'former_customer'));
COMMIT;

-- C. tenants ---------------------------------------------------------------------
BEGIN;
SET LOCAL lock_timeout = '5s';

ALTER TABLE tenants DROP CONSTRAINT IF EXISTS tenants_plan_check;
ALTER TABLE tenants ADD CONSTRAINT tenants_plan_check
    CHECK (plan IN ('free', 'trial', 'demo', 'starter', 'growth', 'business'));

-- No index: only read by a periodic sweep over a table of a few thousand rows;
-- add one if that ever stops being true. A constant-default ADD COLUMN does not
-- rewrite the table in Postgres 11+.
ALTER TABLE tenants
    ADD COLUMN IF NOT EXISTS last_active_at timestamptz NOT NULL DEFAULT now();
COMMIT;

-- D. monthly order counter (new table, locks nothing existing but the tenants FK)
BEGIN;
SET LOCAL lock_timeout = '5s';

CREATE TABLE IF NOT EXISTS tenant_order_months (
    tenant_id uuid    NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    -- First day of the month the counts belong to.
    month     date    NOT NULL,
    orders    integer NOT NULL DEFAULT 0 CONSTRAINT tenant_order_months_orders_check  CHECK (orders  >= 0),
    blocked   integer NOT NULL DEFAULT 0 CONSTRAINT tenant_order_months_blocked_check CHECK (blocked >= 0),
    -- PK serves two queries: the atomic upsert "bump this tenant's counter for
    -- this month" and the limit check "read this tenant's row for this month".
    -- Its leading column tenant_id also serves the foreign key (cascade delete
    -- of a tenant finds its rows), so no separate FK index is needed.
    PRIMARY KEY (tenant_id, month)
);

-- Same reasoning as 046/054: the backend connects as postgres and bypasses RLS;
-- enabling it with no policies only walls off Supabase's public REST roles.
ALTER TABLE tenant_order_months ENABLE ROW LEVEL SECURITY;
COMMIT;

-- Check afterwards (expect 3 constraint rows, 1 column row):
--   SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
--    WHERE conname IN ('tenants_plan_check','marketing_contacts_lifecycle_check','notifications_type_check');
--   SELECT column_name, column_default FROM information_schema.columns
--    WHERE table_name='tenants' AND column_name='last_active_at';
