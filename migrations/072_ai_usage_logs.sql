-- =============================================================================
-- AI usage log: one row per AI call, so the platform operator can see exactly
-- what merchants asked, what the model answered, what it cost in tokens and
-- credits, and what failed. Before this, only the credit ledgers
-- (ai_image_credit_transactions / chat_credit_transactions) existed, which
-- record money moving but never the request or the response.
--
-- kind:
--   chat            the dashboard chat assistant (one row per user message,
--                   with the history, the reply and any tools it called)
--   generate_text   the "Generate / Regenerate" copywriting buttons
--   theme_suggest   the Colors/Brand AI suggestion
--   image_generate  a new AI image
--   image_edit      an edit of an existing image
--
-- input / output / meta are JSONB because each kind has a different shape and
-- this table is an audit trail, not something the app queries by field. The
-- prompt text and reply live inside them, which is what makes this table
-- sensitive: it is only ever read through /superadmin/ai.
--
-- Generated image bytes are NOT stored (they are never kept unless the
-- merchant saves one to the gallery). thumbnail holds a small data-URI preview
-- so an operator can see what the model produced without the table holding
-- full-size images.
--
-- tenant_id cascades (a deleted tenant takes its history with it). user_id
-- SET NULL: the row stays useful for cost tracking if the user is removed.
-- =============================================================================

CREATE TABLE IF NOT EXISTS ai_usage_logs (
    id                uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id         uuid        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id           uuid                 REFERENCES users(id)   ON DELETE SET NULL,
    kind              text        NOT NULL
                                  CHECK (kind IN ('chat', 'generate_text', 'theme_suggest',
                                                  'image_generate', 'image_edit')),
    status            text        NOT NULL DEFAULT 'ok'
                                  CHECK (status IN ('ok', 'error')),
    model             text,
    input             jsonb       NOT NULL DEFAULT '{}'::jsonb,
    output            jsonb       NOT NULL DEFAULT '{}'::jsonb,
    meta              jsonb       NOT NULL DEFAULT '{}'::jsonb,
    error             text,
    thumbnail         text,
    prompt_tokens     integer,
    completion_tokens integer,
    total_tokens      integer,
    gemini_calls      integer     NOT NULL DEFAULT 0,
    latency_ms        integer,
    created_at        timestamptz NOT NULL DEFAULT now()
);

-- INDEX: the superadmin AI feed, newest first across every tenant.
CREATE INDEX IF NOT EXISTS idx_ai_usage_created
    ON ai_usage_logs (created_at DESC);

-- INDEX: the feed filtered to one tenant ("what is this merchant doing with
-- AI?") and the tenant_id foreign key.
CREATE INDEX IF NOT EXISTS idx_ai_usage_tenant_created
    ON ai_usage_logs (tenant_id, created_at DESC);

-- INDEX: the feed filtered by kind (chat vs images) and the per-kind counts
-- in the summary.
CREATE INDEX IF NOT EXISTS idx_ai_usage_kind_created
    ON ai_usage_logs (kind, created_at DESC);

-- INDEX: the user_id foreign key (ON DELETE SET NULL has to find these rows).
CREATE INDEX IF NOT EXISTS idx_ai_usage_user
    ON ai_usage_logs (user_id);
