-- =============================================================================
-- Persists the "I already sent the money" submission from the dashboard
-- Billing page (app/api/billing.py's submit_manual_payment), which until now
-- only ever sent an email and stored nothing — see that function's own
-- docstring. That was fine when a human always read the email and cross-
-- checked the real bKash statement by hand, but there's nothing here for an
-- automated match (an SMS-forwarding app relaying the merchant's own bKash/
-- bank receipt texts to app/api/public.py's bkash_sms_webhook) to compare
-- against. This table is that comparison target: one row per claim, matched
-- by trx_id once a real deposit SMS with the same TrxID arrives.
--
-- Still no payment gateway — this doesn't charge anyone. It just lets a real,
-- already-received deposit (the merchant sent real money to a real bKash/
-- bank account by hand) upgrade the plan automatically instead of waiting for
-- someone to read an email and click a button in Superadmin.
-- =============================================================================

CREATE TABLE IF NOT EXISTS payment_claims (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    plan TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    sender_number TEXT NOT NULL,
    trx_id TEXT NOT NULL,
    note TEXT,
    -- pending -> verified (webhook matched it) or rejected (a human decided
    -- the trx_id was never going to arrive, e.g. merchant mistyped it).
    -- There's no dashboard/superadmin UI for "rejected" yet; it exists so a
    -- stale pending claim can be closed out by hand in the database without
    -- it looking like it's still waiting.
    status TEXT NOT NULL DEFAULT 'pending',
    matched_sms TEXT,
    verified_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- INDEX: bkash_sms_webhook looks up a claim by trx_id on every incoming SMS.
-- Unique because a trx_id is only ever real once — this also rejects a
-- merchant accidentally (or deliberately) submitting the same TrxID twice.
CREATE UNIQUE INDEX IF NOT EXISTS idx_payment_claims_trx_id ON payment_claims (trx_id);

-- INDEX: tenant's own claim history (e.g. a future "your claims" list on the
-- Billing page) filters on tenant_id.
CREATE INDEX IF NOT EXISTS idx_payment_claims_tenant_id ON payment_claims (tenant_id);

-- INDEX: any occasional "what's still unmatched" review filters on status;
-- partial on 'pending' since verified/rejected rows are never queried this way.
CREATE INDEX IF NOT EXISTS idx_payment_claims_pending
    ON payment_claims (created_at)
    WHERE status = 'pending';
