-- =============================================================================
-- Softune Pay: a merchant's own phone confirms manual bKash/Nagad/Rocket
-- payments automatically.
--
-- The free Android app (pay/ in this repo's sibling folder) reads the "you
-- received Tk X, TrxID Y" SMS on the merchant's phone and posts it here. The
-- server parses it and, when a pending manual order carries the same
-- transaction ID and total, marks that order paid. No bKash/Nagad merchant
-- account and no fees: it works on personal numbers.
--
-- pay_devices: one row per paired phone. The phone authenticates with a
--   random token shown to it once at pairing; only its SHA-256 is stored, so a
--   database leak can't impersonate a phone (same idea as hash_otp).
-- pay_sms: one row per payment SMS a phone sent in. Kept whether or not it
--   matched an order, so "money arrived but no order has this ID" is visible
--   to the merchant instead of silently dropped.
--
-- Pairing codes live in Redis for 10 minutes, not here: they're worthless
-- once used or expired.
-- =============================================================================

CREATE TABLE IF NOT EXISTS pay_devices (
    id            uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     uuid        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    site_id       uuid        NOT NULL REFERENCES sites(id)   ON DELETE CASCADE,
    name          text        NOT NULL DEFAULT 'Android phone',
    token_hash    text        NOT NULL,
    app_version   text,
    -- What the phone last reported: which SIM it watches and which wallets
    -- are switched on. Display only; the server never trusts it for matching.
    sim_carrier   text,
    sim_number    text,
    providers     jsonb       NOT NULL DEFAULT '["bkash","nagad","rocket"]'::jsonb,
    watching      boolean     NOT NULL DEFAULT true,
    last_seen_at  timestamptz,
    revoked_at    timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_pay_devices_token_hash UNIQUE (token_hash)
);

DROP TRIGGER IF EXISTS trg_pay_devices_updated_at ON pay_devices;
CREATE TRIGGER trg_pay_devices_updated_at
    BEFORE UPDATE ON pay_devices
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- INDEX: the dashboard's "connected phones" list for one site.
-- Also the foreign-key index for site deletes (CLAUDE.md rule 6).
CREATE INDEX IF NOT EXISTS idx_pay_devices_site ON pay_devices (site_id, created_at DESC);
-- INDEX: foreign key; tenant deletes cascade without a sequential scan.
CREATE INDEX IF NOT EXISTS idx_pay_devices_tenant ON pay_devices (tenant_id);
-- (token_hash lookups on every phone request use uq_pay_devices_token_hash.)


CREATE TABLE IF NOT EXISTS pay_sms (
    id            uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     uuid        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    site_id       uuid        NOT NULL REFERENCES sites(id)   ON DELETE CASCADE,
    -- SET NULL: unpairing a phone must not erase the payment history it built.
    device_id     uuid        REFERENCES pay_devices(id) ON DELETE SET NULL,
    provider      text        NOT NULL CHECK (provider IN ('bkash', 'nagad', 'rocket')),
    trx_id        citext      NOT NULL,
    amount_cents  integer     NOT NULL CHECK (amount_cents >= 0),
    sender_number text,
    raw_text      text        NOT NULL,
    sms_at        timestamptz NOT NULL DEFAULT now(),
    -- matched: an order was marked paid from this SMS.
    -- unmatched: no pending order had this transaction ID (yet). Checkout
    --   re-checks these, since shoppers often pay before placing the order.
    -- mismatch: an order has this ID but a different total; needs a human.
    status        text        NOT NULL DEFAULT 'unmatched'
                              CHECK (status IN ('matched', 'unmatched', 'mismatch')),
    -- SET NULL: order history is immutable, but if an order is ever removed
    -- the money record must survive.
    order_id      uuid        REFERENCES orders(id) ON DELETE SET NULL,
    matched_at    timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now(),
    -- One transaction can only ever be counted once per store: a phone
    -- resending, or a forged repeat of a real SMS, can't pay a second order.
    CONSTRAINT uq_pay_sms_site_trx UNIQUE (site_id, provider, trx_id)
);

-- INDEX: the app's and dashboard's payment list — one site, newest first.
CREATE INDEX IF NOT EXISTS idx_pay_sms_site_created ON pay_sms (site_id, created_at DESC);
-- INDEX: checkout looks for an unmatched SMS by transaction ID when an order is
-- placed after the money already arrived. Partial: only unmatched rows matter.
CREATE INDEX IF NOT EXISTS idx_pay_sms_site_trx_unmatched
    ON pay_sms (site_id, trx_id) WHERE status = 'unmatched';
-- INDEX: foreign keys (CLAUDE.md rule 6).
CREATE INDEX IF NOT EXISTS idx_pay_sms_tenant ON pay_sms (tenant_id);
CREATE INDEX IF NOT EXISTS idx_pay_sms_device ON pay_sms (device_id);
CREATE INDEX IF NOT EXISTS idx_pay_sms_order  ON pay_sms (order_id);


-- INDEX: an incoming SMS finds its order by the transaction ID the shopper
-- typed at checkout (orders.meta->>'transaction_id'). Expression index so this
-- is an index lookup, not a scan of every order; partial on pending, since only
-- unpaid orders can be confirmed. upper() because shoppers type IDs in any case.
CREATE INDEX IF NOT EXISTS idx_orders_site_pending_trx
    ON orders (site_id, upper(meta ->> 'transaction_id'))
    WHERE status = 'pending' AND meta ? 'transaction_id';


ALTER TABLE pay_devices ENABLE ROW LEVEL SECURITY;
ALTER TABLE pay_sms     ENABLE ROW LEVEL SECURITY;
