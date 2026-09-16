-- =============================================================================
-- Purchasable chat-credit balance — extends the free daily text-chat
-- allowance (app/ai.py's PLAN_AI_DAILY_CAP, a Redis-only counter with no DB
-- column) rather than replacing it. The daily free count still resets every
-- night and still costs nothing; once a tenant burns through it for the
-- day, _check_ai_access falls back to spending from THIS balance instead of
-- a hard 429, one credit per request. Exactly the same shape as
-- migrations/066's ai_image_credits/ai_image_credit_transactions — a
-- separate table, not a shared one, since a chat request and an image
-- generation are priced on completely different scales (a Flash-Lite text
-- call costs a fraction of a cent; see app/chat_credits.py for the real
-- per-credit price this was set against).
-- =============================================================================

ALTER TABLE tenants ADD COLUMN IF NOT EXISTS chat_credits integer NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS chat_credit_transactions (
    id            uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     uuid        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    delta         integer     NOT NULL,
    reason        text        NOT NULL
                              CHECK (reason IN ('purchase', 'grant', 'spend', 'refund')),
    balance_after integer    NOT NULL,
    reference     text,
    created_at    timestamptz NOT NULL DEFAULT now()
);

-- INDEX: the tenant's own transaction history, newest first — same query
-- shape as ai_image_credit_transactions' own index (migrations/066).
CREATE INDEX IF NOT EXISTS idx_chat_credit_tx_tenant_created
    ON chat_credit_transactions (tenant_id, created_at DESC);
