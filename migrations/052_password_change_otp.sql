-- =============================================================================
--  052_password_change_otp.sql — OTP-verified password change
-- =============================================================================
--  Separate columns from login_otp_* (migration 044) on purpose: a password
--  change requested mid-login-OTP-challenge must not stomp on (or be
--  stomped by) that in-progress code — they're two independent challenges
--  that happen to share the same generate_otp()/hash_otp() mechanism.
-- =============================================================================

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS password_otp_hash text,
    ADD COLUMN IF NOT EXISTS password_otp_expires_at timestamptz,
    ADD COLUMN IF NOT EXISTS password_otp_attempts int NOT NULL DEFAULT 0;
