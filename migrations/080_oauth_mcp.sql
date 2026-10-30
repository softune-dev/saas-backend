-- =============================================================================
-- OAuth for the ChatGPT plugin (remote MCP server).
--
-- ChatGPT connects to a merchant's store through an MCP server on this API
-- (app/mcp_server.py). ChatGPT requires OAuth 2.1 with PKCE, so this API is
-- also a small OAuth authorization server (app/oauth_server.py):
--   oauth_clients  who may ask for access (ChatGPT registers itself once per
--                  connection with Dynamic Client Registration)
--   oauth_codes    one-time authorization codes (10 minutes, single use)
--   oauth_tokens   the access/refresh token pairs a merchant approved
--
-- Tokens and codes are random secrets; only their SHA-256 hash is stored, so
-- a database leak does not hand out working tokens. Every token is bound to
-- one tenant (and the user who approved it) and to the MCP address it was
-- issued for (`resource`), so it cannot be replayed against another service.
--
-- oauth_clients is a global registry (not tenant-owned): ChatGPT, not a
-- merchant, creates those rows. The other two cascade with the tenant, so
-- deleting a store revokes everything it ever approved.
-- =============================================================================

CREATE TABLE IF NOT EXISTS oauth_clients (
    id            uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id     text        NOT NULL UNIQUE,
    client_name   text,
    redirect_uris text[]      NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS oauth_codes (
    id             uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id      uuid        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id        uuid        NOT NULL REFERENCES users(id)   ON DELETE CASCADE,
    client_id      text        NOT NULL,
    code_hash      text        NOT NULL UNIQUE,
    redirect_uri   text        NOT NULL,
    code_challenge text        NOT NULL,
    resource       text        NOT NULL,
    scope          text        NOT NULL,
    expires_at     timestamptz NOT NULL,
    used_at        timestamptz,
    created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS oauth_tokens (
    id                 uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id          uuid        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id            uuid        NOT NULL REFERENCES users(id)   ON DELETE CASCADE,
    client_id          text        NOT NULL,
    access_hash        text        NOT NULL UNIQUE,
    refresh_hash       text        NOT NULL UNIQUE,
    resource           text        NOT NULL,
    scope              text        NOT NULL,
    access_expires_at  timestamptz NOT NULL,
    refresh_expires_at timestamptz NOT NULL,
    revoked_at         timestamptz,
    last_used_at       timestamptz,
    created_at         timestamptz NOT NULL DEFAULT now()
);

-- Foreign-key indexes (CLAUDE.md rule 6). The tenant one also serves the
-- dashboard's "connected apps" list, which filters on tenant_id.
CREATE INDEX IF NOT EXISTS ix_oauth_codes_tenant   ON oauth_codes  (tenant_id);
CREATE INDEX IF NOT EXISTS ix_oauth_codes_user     ON oauth_codes  (user_id);
CREATE INDEX IF NOT EXISTS ix_oauth_tokens_tenant  ON oauth_tokens (tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_oauth_tokens_user    ON oauth_tokens (user_id);

-- Supabase exposes public tables through its REST API unless row-level
-- security is on. The API connects as the table owner and is unaffected; this
-- only keeps the public anon key out of these tables. (The project's
-- ensure_rls trigger does the same automatically; stated here so this file
-- is correct on its own.)
ALTER TABLE oauth_clients ENABLE ROW LEVEL SECURITY;
ALTER TABLE oauth_codes   ENABLE ROW LEVEL SECURITY;
ALTER TABLE oauth_tokens  ENABLE ROW LEVEL SECURITY;
