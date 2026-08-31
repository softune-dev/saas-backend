-- =============================================================================
--  044_login_otp_and_trusted_devices.sql — device-remembered login 2FA
-- =============================================================================
--  OTP is required on login ONLY from a device/browser this user hasn't
--  logged in from before. A verified device gets remembered (see
--  trusted_devices) so returning logins skip the OTP step — "not always,
--  only when needed" per the actual product decision, not OTP-every-time.
--
--  login_otp_* mirrors leads.otp_hash/otp_expires_at exactly (same reasons:
--  hashed not plaintext, short expiry, attempt cap) — see app/security.py's
--  generate_otp/hash_otp, shared by both app/api/auth.py and app/api/leads.py.
-- =============================================================================

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS login_otp_hash text,
    ADD COLUMN IF NOT EXISTS login_otp_expires_at timestamptz,
    ADD COLUMN IF NOT EXISTS login_otp_attempts int NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS trusted_devices (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    -- SHA-256 of the client-generated device id (localStorage, never a
    -- cookie) — hashed same instinct as courier/payment credentials: a DB
    -- leak alone shouldn't hand out working device tokens.
    device_hash text        NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    last_used_at timestamptz NOT NULL DEFAULT now(),
    expires_at  timestamptz NOT NULL,

    CONSTRAINT uq_trusted_devices_user_device UNIQUE (user_id, device_hash)
);

-- Every login checks "is this device_hash trusted for this user" — this is
-- the query that runs on literally every login attempt, so it needs to be
-- fast; the unique constraint above already creates the index this serves.
