-- =============================================================================
-- AI image generation — a real purchasable credit balance, separate from
-- the daily-reset AI text-chat counter (app/ai.py's PLAN_AI_DAILY_CAP,
-- Redis-only, no DB column). Image generation calls a real per-image-priced
-- Gemini model (see app/ai_images.py) so it needs an actual persisted
-- balance that only ever moves by an explicit transaction — never a
-- silent daily reset like the text counter.
--
-- ai_image_credits is the current spendable balance. Every change to it —
-- purchase, spend, refund, manual grant — goes through
-- ai_image_credit_transactions as well, so the balance is always
-- reconstructable/auditable (a support dispute is "read the ledger", not
-- "trust the column"). delta is signed (+ for purchase/grant/refund, - for
-- spend); balance_after is a snapshot at that point in time.
-- =============================================================================

ALTER TABLE tenants ADD COLUMN IF NOT EXISTS ai_image_credits integer NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS ai_image_credit_transactions (
    id           uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    uuid        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    delta        integer     NOT NULL,
    reason       text        NOT NULL
                              CHECK (reason IN ('purchase', 'grant', 'generate', 'edit', 'refund')),
    balance_after integer    NOT NULL,
    -- Free-text context: a preset id + tier for generate/edit, an invoice
    -- number or admin note for purchase/grant, the original transaction's
    -- id for a refund. Not a foreign key on purpose — the things it can
    -- reference vary by reason, and this is an audit trail, not a join target.
    reference    text,
    created_at   timestamptz NOT NULL DEFAULT now()
);

-- INDEX: the tenant's own transaction history, newest first — what
-- GET /ai/images/transactions (and any future support-dispute lookup) reads.
CREATE INDEX IF NOT EXISTS idx_ai_image_credit_tx_tenant_created
    ON ai_image_credit_transactions (tenant_id, created_at DESC);
