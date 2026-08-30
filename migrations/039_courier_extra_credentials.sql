-- =============================================================================
--  039_courier_extra_credentials.sql — room for a 3rd/4th courier credential
-- =============================================================================
--  Steadfast/RedX fit api_key_encrypted + secret_key_encrypted. Pathao needs
--  four: client_id, client_secret, username, password. Rather than keep
--  widening this table per-provider, extra_encrypted holds a single Fernet
--  ciphertext blob of a small JSON object for whatever a provider needs
--  beyond the first two columns — nullable because Steadfast/RedX don't use
--  it at all.
-- =============================================================================

ALTER TABLE courier_connections
    ADD COLUMN IF NOT EXISTS extra_encrypted text;
