-- =============================================================================
--  012_courier_connections.sql — merchant courier accounts (Steadfast first)
-- =============================================================================
--  Run AFTER 003_commerce.sql.
--
--  Backs the dashboard's /courier page (see docs/TODO_PRODUCT_PAGE_REBUILD.md
--  and dashboard/lib/api/courier.ts, which this table's shape was designed to
--  satisfy exactly — GET/POST/DELETE on /sites/{site_id}/couriers).
--
--  Each site connects its OWN courier merchant account — never a shared
--  platform-wide key. One row per (site, provider): a site can have at most
--  one Steadfast connection, one Pathao, one RedX.
--
--  Credentials are NEVER stored in plaintext. api_key_encrypted /
--  secret_key_encrypted hold Fernet ciphertext (see app/courier_crypto.py).
--  api_key_hint is the only credential fragment ever returned to the client —
--  enough for a merchant to recognize "yes that's my key", never enough to
--  reconstruct it.
-- =============================================================================

CREATE TABLE IF NOT EXISTS courier_connections (
    id                  uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           uuid        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    site_id             uuid        NOT NULL REFERENCES sites(id)   ON DELETE CASCADE,

    provider            text        NOT NULL
                                    CHECK (provider IN ('steadfast', 'pathao', 'redx')),

    status              text        NOT NULL DEFAULT 'connected'
                                    CHECK (status IN ('connected', 'error', 'disabled')),

    label               text,

    -- Ciphertext only — see app/courier_crypto.py. Never selected into a
    -- response model; app/schemas.py's CourierConnectionOut has no field for
    -- these columns at all, so a schema mistake can't leak them either.
    api_key_encrypted    text        NOT NULL,
    secret_key_encrypted text        NOT NULL,

    -- Display-safe fragment of the API key, e.g. "••••••a1b2".
    api_key_hint        text        NOT NULL,

    -- Steadfast sandbox vs production base URL override. Null = platform default.
    base_url            text,

    -- Set when the credentials were last confirmed to work against the
    -- provider's own API (see app/steadfast.py's verify call). Null means
    -- never successfully verified — status='error' if a verify attempt failed.
    last_verified_at    timestamptz,

    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT uq_courier_connections_site_provider UNIQUE (site_id, provider)
);

-- INDEX: "does this site have any courier connections" — the /courier page's
-- only query, and the ownership check every write goes through.
CREATE INDEX IF NOT EXISTS idx_courier_connections_site
    ON courier_connections (site_id);

CREATE INDEX IF NOT EXISTS idx_courier_connections_tenant
    ON courier_connections (tenant_id);

DROP TRIGGER IF EXISTS trg_courier_connections_updated_at ON courier_connections;
CREATE TRIGGER trg_courier_connections_updated_at
    BEFORE UPDATE ON courier_connections
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
