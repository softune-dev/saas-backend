-- =============================================================================
-- Email campaigns: marketing emails the platform operator sends from the
-- superadmin "Email campaigns" page. Email only; there is deliberately no
-- WhatsApp or SMS channel.
--
-- One row per send. The audience is stored as the filter that was used
-- (audience jsonb) plus the number of recipients it resolved to at send time,
-- so history shows exactly who a campaign targeted even after tenants change
-- plan. Each recipient becomes one JOB_SEND_EMAIL on the queue, so a slow SMTP
-- server never blocks the request and the worker retries the same way it does
-- for every other email; delivery per recipient is not tracked here.
--
-- created_by SET NULL: a campaign stays in the history if the operator's user
-- row is later removed.
-- =============================================================================

CREATE TABLE IF NOT EXISTS email_campaigns (
    id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    created_by      uuid                 REFERENCES users(id) ON DELETE SET NULL,
    subject         text        NOT NULL,
    headline        text        NOT NULL DEFAULT '',
    body            text        NOT NULL,
    cta_label       text,
    cta_url         text,
    audience        jsonb       NOT NULL DEFAULT '{}'::jsonb,
    recipient_count integer     NOT NULL DEFAULT 0,
    status          text        NOT NULL DEFAULT 'sent'
                                CHECK (status IN ('draft', 'sent')),
    sent_at         timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- INDEX: the campaign history list, newest first.
CREATE INDEX IF NOT EXISTS idx_email_campaigns_created
    ON email_campaigns (created_at DESC);

-- INDEX: the created_by foreign key (ON DELETE SET NULL has to find these rows).
CREATE INDEX IF NOT EXISTS idx_email_campaigns_created_by
    ON email_campaigns (created_by);
