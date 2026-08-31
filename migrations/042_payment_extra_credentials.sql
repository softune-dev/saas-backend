-- =============================================================================
--  042_payment_extra_credentials.sql — room for a 3rd/4th payment credential
-- =============================================================================
--  Same reasoning as migrations/039 (courier's extra_encrypted): bKash needs
--  api_key/secret_key (app_key/app_secret) PLUS a username/password pair,
--  and Nagad needs a merchant private key PLUS Nagad's own public key
--  (both PEM blocks). Rather than keep widening this table per-provider,
--  extra_encrypted holds a single Fernet ciphertext blob of a small JSON
--  object for whatever a provider needs beyond the first two columns —
--  nullable because cod/manual/sslcommerz don't use it.
-- =============================================================================

ALTER TABLE payment_connections
    ADD COLUMN IF NOT EXISTS extra_encrypted text;

-- No provider here has ever had a real verification moment before now
-- (bkash.verify_credentials is the first) — nullable, stays null for
-- cod/manual/sslcommerz/nagad/rocket, which don't verify live.
ALTER TABLE payment_connections
    ADD COLUMN IF NOT EXISTS last_verified_at timestamptz;
