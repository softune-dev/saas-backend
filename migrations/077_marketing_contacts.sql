-- =============================================================================
-- Marketing contacts: a permanent record of everyone who ever signed up, kept
-- for future outreach even after their account is gone.
--
-- Trial accounts that never convert are deleted by the worker after a grace
-- period (app/worker.py sweep_expired_trials), and the operator can delete any
-- tenant. Every table that holds a person's name, email and phone cascades from
-- tenants, so those details used to disappear with the account. This table is
-- deliberately OUTSIDE that cascade: no foreign keys at all. tenant_id is a
-- plain uuid, a remembered pointer, not a constraint, so deleting a tenant
-- never touches a row here.
--
-- One row per email (citext, unique), so a person who signs up twice is one
-- contact. lifecycle:
--   lead             opened the public demo, no account
--   trial            has an account on a trial
--   customer         has an account on a paid plan
--   churned          account was deleted and they never paid
--   former_customer  account was deleted after they had paid
--
-- unsubscribed_at is respected by the email campaign page: an unsubscribed
-- contact is never emailed again, whatever audience a campaign targets.
--
-- The backfill below captures everyone who exists today, so nothing already
-- scheduled for deletion is lost. It only inserts rows that are not already
-- present, so re-running this file is harmless.
-- =============================================================================

CREATE TABLE IF NOT EXISTS marketing_contacts (
    id                  uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    email               citext      NOT NULL UNIQUE,
    full_name           text,
    phone               text,
    shop_name           text,
    subdomain           text,
    source              text        NOT NULL DEFAULT 'signup'
                                    CHECK (source IN ('signup', 'demo', 'manual')),
    lifecycle           text        NOT NULL DEFAULT 'trial'
                                    CHECK (lifecycle IN ('lead', 'trial', 'customer', 'churned', 'former_customer')),
    plan                text,
    ever_paid           boolean     NOT NULL DEFAULT false,
    tenant_id           uuid,
    signed_up_at        timestamptz,
    converted_at        timestamptz,
    account_deleted_at  timestamptz,
    last_login_at       timestamptz,
    unsubscribed_at     timestamptz,
    notes               text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);

DROP TRIGGER IF EXISTS trg_marketing_contacts_updated_at ON marketing_contacts;
CREATE TRIGGER trg_marketing_contacts_updated_at
    BEFORE UPDATE ON marketing_contacts
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- INDEX: the contacts page filters by lifecycle and lists newest signups first.
CREATE INDEX IF NOT EXISTS idx_marketing_contacts_lifecycle
    ON marketing_contacts (lifecycle, created_at DESC);

-- INDEX: the daily sync finds a tenant's contacts by the remembered tenant_id.
CREATE INDEX IF NOT EXISTS idx_marketing_contacts_tenant
    ON marketing_contacts (tenant_id)
    WHERE tenant_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Backfill 1: every real user that exists right now (not the shared demo
-- tenant, not platform staff).
-- ---------------------------------------------------------------------------
INSERT INTO marketing_contacts (
    email, full_name, phone, shop_name, subdomain, source, lifecycle, plan,
    ever_paid, tenant_id, signed_up_at, converted_at, last_login_at
)
SELECT
    u.email,
    u.full_name,
    u.phone,
    t.name,
    (SELECT s.subdomain FROM sites s WHERE s.tenant_id = t.id ORDER BY s.created_at LIMIT 1),
    'signup',
    CASE WHEN t.plan IN ('starter', 'growth', 'business') THEN 'customer' ELSE 'trial' END,
    t.plan,
    (t.plan IN ('starter', 'growth', 'business')
        OR EXISTS (SELECT 1 FROM invoices i WHERE i.tenant_id = t.id AND i.amount_cents > 0)),
    t.id,
    u.created_at,
    (SELECT MIN(i.issued_at) FROM invoices i WHERE i.tenant_id = t.id AND i.amount_cents > 0),
    u.last_login_at
FROM users u
JOIN tenants t ON t.id = u.tenant_id
WHERE t.plan <> 'demo'
  AND u.is_superadmin IS NOT TRUE
ON CONFLICT (email) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Backfill 2: people who opened the demo but never made an account.
-- ---------------------------------------------------------------------------
INSERT INTO marketing_contacts (email, source, lifecycle, signed_up_at)
SELECT d.email, 'demo', 'lead', d.first_requested_at
FROM demo_access_requests d
ON CONFLICT (email) DO NOTHING;
