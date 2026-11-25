-- =============================================================================
-- 090_inactivity.sql: (A) which inactivity warnings a Free account has been
-- sent, and (B) the record kept when an account or store is deleted.
--
-- RUN BEFORE the new backend is deployed (safe to run now): it only adds two
-- tables that the old code never reads. Nothing is changed or deleted. Safe to run
-- twice. (A separate table, not columns on tenants: if this file were ever
-- missed, only the inactivity job would fail, not every query on tenants.)
--
-- A row exists only once a first warning email has really been sent.
-- app/inactivity.py deletes a Free account only when its row shows all three,
-- for the activity time the account still has.
-- =============================================================================

BEGIN;
SET LOCAL lock_timeout = '5s';

CREATE TABLE IF NOT EXISTS tenant_inactivity (
    -- The primary key also serves the foreign key: the job reads rows by
    -- tenant, and deleting a tenant finds its row through it.
    tenant_id  uuid        PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
    stage      smallint    NOT NULL CONSTRAINT tenant_inactivity_stage_check CHECK (stage BETWEEN 1 AND 3),
    -- When the latest warning was sent.
    warned_at  timestamptz NOT NULL,
    -- tenants.last_active_at as it was when the warnings were counted. Any new
    -- activity moves that column, which voids them.
    warned_for timestamptz NOT NULL
);

-- Same reasoning as 087: the backend connects as postgres and bypasses RLS;
-- enabling it with no policies only walls off Supabase's public REST roles.
ALTER TABLE tenant_inactivity ENABLE ROW LEVEL SECURITY;
COMMIT;

-- B. ----------------------------------------------------------------------------
BEGIN;
SET LOCAL lock_timeout = '5s';

CREATE TABLE IF NOT EXISTS deleted_accounts (
    id            uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Deliberately NO foreign key: this row must outlive the tenant.
    tenant_id     uuid        NOT NULL,
    -- 'inactive', 'unpaid', 'admin' or 'store deleted'.
    reason        text        NOT NULL,
    email         text,
    full_name     text,
    phone         text,
    shop_name     text,
    subdomain     text,
    custom_domain text,
    plan          text,
    ever_paid     boolean     NOT NULL DEFAULT false,
    -- The account, users, store settings, pages, categories, products and
    -- events (app/account_archive.py). No secrets, orders or shopper data.
    snapshot      jsonb       NOT NULL,
    deleted_at    timestamptz NOT NULL DEFAULT now()
);

-- Serves "find this returning person's old store", looked up by email.
CREATE INDEX IF NOT EXISTS ix_deleted_accounts_email ON deleted_accounts (lower(email));

-- Personal data: never readable through Supabase's public REST roles.
ALTER TABLE deleted_accounts ENABLE ROW LEVEL SECURITY;
COMMIT;

-- Check afterwards (expect 2 rows):
--   SELECT tablename FROM pg_tables WHERE tablename IN ('tenant_inactivity', 'deleted_accounts');
