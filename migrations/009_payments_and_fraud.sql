-- =============================================================================
--  009_payments_and_fraud.sql — payment method connections + fraud blocklist
-- =============================================================================
--  Run AFTER 008_bazaar_template.sql.
--
--  Backs two new dashboard pages: /payments (dashboard/components/payments/)
--  and Settings → Fraud Protection (dashboard/components/fraud/). Both were
--  built UI-first against mock state; this is the real storage layer.
--
--  PAYMENT CONNECTIONS — mirrors courier_connections' shape almost exactly
--  (see 012_courier_connections.sql), same reasoning: one row per
--  (site, provider), credentials (when a provider has any) are Fernet
--  ciphertext only, never plaintext. Reuses app/courier_crypto.py's Fernet
--  key rather than a separate one — same trust boundary (app config, not
--  the database), no reason to manage two keys for the same sensitivity
--  level.
--
--  Two providers (cod, manual) have NO credentials at all — `config` holds
--  their real settings instead (COD fee, manual payment number + accepted
--  wallets). Gateway providers (bkash, nagad, sslcommerz, rocket) will use
--  the encrypted columns once real merchant accounts exist; the columns
--  exist now so connecting one later doesn't need a schema change.
--
--  FRAUD BLOCKLIST — a merchant-maintained list of phone numbers, not a
--  computed risk score (there is no order history to score from yet — see
--  the design discussion that dropped the original risk-table plan). One
--  row per (site, phone).
--
--  FRAUD RULES — deliberately NOT a new table. Follows the same pattern as
--  sites.shipping/sites.faqs/sites.legal: a single JSONB column on `sites`,
--  read/written through the existing generic PATCH /sites/{id} endpoint.
--  Three toggleable rules, each evaluated against the CURRENT order only
--  (see dashboard/components/fraud/fraud-data.ts's FRAUD_RULES) — no
--  historical data needed.
-- =============================================================================

ALTER TABLE sites
    ADD COLUMN IF NOT EXISTS fraud_rules jsonb NOT NULL DEFAULT '{}'::jsonb;


CREATE TABLE IF NOT EXISTS payment_connections (
    id                   uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id            uuid        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    site_id              uuid        NOT NULL REFERENCES sites(id)   ON DELETE CASCADE,

    provider             text        NOT NULL
                                     CHECK (provider IN ('cod', 'manual', 'bkash', 'nagad', 'sslcommerz', 'rocket')),

    status               text        NOT NULL DEFAULT 'connected'
                                     CHECK (status IN ('connected', 'error', 'disabled')),

    label                text,

    -- Non-secret provider config: COD fee, manual payment number + accepted
    -- wallets, gateway merchant/store id. Never holds api_key/secret_key —
    -- those two go through the encrypted columns below or not at all.
    config               jsonb       NOT NULL DEFAULT '{}'::jsonb,

    -- Ciphertext only, nullable — cod/manual never populate these; a
    -- connected gateway always does. Mirrors courier_connections exactly,
    -- see app/courier_crypto.py.
    api_key_encrypted    text,
    secret_key_encrypted text,
    api_key_hint         text,

    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT uq_payment_connections_site_provider UNIQUE (site_id, provider)
);

CREATE INDEX IF NOT EXISTS idx_payment_connections_site
    ON payment_connections (site_id);
CREATE INDEX IF NOT EXISTS idx_payment_connections_tenant
    ON payment_connections (tenant_id);

DROP TRIGGER IF EXISTS trg_payment_connections_updated_at ON payment_connections;
CREATE TRIGGER trg_payment_connections_updated_at
    BEFORE UPDATE ON payment_connections
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();


CREATE TABLE IF NOT EXISTS fraud_blocklist (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    site_id     uuid        NOT NULL REFERENCES sites(id)   ON DELETE CASCADE,

    phone       text        NOT NULL,
    note        text        NOT NULL DEFAULT '',

    created_at  timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT uq_fraud_blocklist_site_phone UNIQUE (site_id, phone)
);

-- INDEX: "is this phone blocked for this site" — the exact lookup checkout
-- enforcement will run once a real public checkout endpoint exists.
CREATE INDEX IF NOT EXISTS idx_fraud_blocklist_site
    ON fraud_blocklist (site_id);
CREATE INDEX IF NOT EXISTS idx_fraud_blocklist_tenant
    ON fraud_blocklist (tenant_id);
