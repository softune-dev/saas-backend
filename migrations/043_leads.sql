-- =============================================================================
--  043_leads.sql — prospects who signed up but haven't bought yet
-- =============================================================================
--  Deliberately NOT a tenant/user row — a lead has no site, no billing, no
--  real account. This is a separate, much smaller record that exists purely
--  to (a) get them a demo login without a real account, (b) let the
--  superadmin panel see who's interested, and (c) capture a purchase
--  request to follow up on by email/WhatsApp/phone.
--
--  One row per email, walked through a small linear status funnel:
--  signed_up -> otp_verified -> profile_complete -> demo_accessed (optional,
--  can repeat) -> purchase_requested. Nothing here ever becomes a real
--  `users`/`tenants` row automatically — converting a lead into a paying
--  customer is still the superadmin's own POST /superadmin/tenants
--  (scripts/create_account.py's replacement), a deliberate human step.
-- =============================================================================

CREATE TABLE IF NOT EXISTS leads (
    id                   uuid        PRIMARY KEY DEFAULT gen_random_uuid(),

    email                citext      NOT NULL UNIQUE,
    password_hash        text        NOT NULL,

    full_name            text,
    phone                text,
    shop_name            text,
    shop_category        text,

    status               text        NOT NULL DEFAULT 'signed_up'
                                     CHECK (status IN (
                                        'signed_up', 'otp_verified', 'profile_complete',
                                        'demo_accessed', 'purchase_requested'
                                     )),

    -- OTP: hashed, never plaintext (same instinct as password_hash) — a 6
    -- digit code is low-entropy, so this is a plain SHA-256 hash, not
    -- bcrypt; the real protection is the short expiry + attempt cap below,
    -- not the hash algorithm.
    otp_hash             text,
    otp_expires_at       timestamptz,
    otp_attempts         int         NOT NULL DEFAULT 0,

    demo_accessed_at     timestamptz,
    purchase_requested_at timestamptz,

    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now()
);

DROP TRIGGER IF EXISTS trg_leads_updated_at ON leads;
CREATE TRIGGER trg_leads_updated_at
    BEFORE UPDATE ON leads
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- Superadmin's leads list sorts by recency — see app/api/superadmin.py.
CREATE INDEX IF NOT EXISTS idx_leads_created_at ON leads (created_at DESC);
