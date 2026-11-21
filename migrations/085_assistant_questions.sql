-- =============================================================================
-- assistant_questions: shopper questions the store assistant could not answer.
--
-- The merchant's to-do list for making the assistant better: each row is one
-- distinct question (repeats are counted, not duplicated), and answering it
-- turns it into one of the merchant's own written answers
-- (sites.assistant.qa, migrations/084), so the assistant has it from then on.
--
-- Privacy: only the question text is kept, trimmed to 200 characters, with
-- long digit runs (phone numbers) masked before it is stored (see
-- app/assistant/service.py record_unanswered). No IP, no cookie, no account.
--
-- Bounded: app code stops adding rows past MAX_OPEN_PER_SITE for a site, so
-- an anonymous visitor typing random text cannot grow this table forever.
-- =============================================================================

CREATE TABLE IF NOT EXISTS assistant_questions (
    id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    site_id         uuid        NOT NULL REFERENCES sites(id)   ON DELETE CASCADE,
    -- The question as a shopper typed it (the first time it was seen).
    question        text        NOT NULL,
    -- Normalised words in sorted order, so "delivery to sylhet?" and
    -- "sylhet delivery" count as one question.
    question_key    text        NOT NULL,
    lang            text        NOT NULL DEFAULT 'en' CHECK (lang IN ('en', 'bn')),
    times_asked     integer     NOT NULL DEFAULT 1,
    status          text        NOT NULL DEFAULT 'open'
                                CHECK (status IN ('open', 'answered', 'dismissed')),
    first_asked_at  timestamptz NOT NULL DEFAULT now(),
    last_asked_at   timestamptz NOT NULL DEFAULT now(),
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    -- One row per distinct question per store. Also the ON CONFLICT target for
    -- the "count another ask" upsert, and its leading site_id column serves
    -- the site_id foreign key.
    CONSTRAINT uq_assistant_questions_site_key UNIQUE (site_id, question_key)
);

DROP TRIGGER IF EXISTS trg_assistant_questions_updated_at ON assistant_questions;
CREATE TRIGGER trg_assistant_questions_updated_at
    BEFORE UPDATE ON assistant_questions
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- INDEX: the dashboard inbox: one site, open questions, most asked first.
-- Equality columns first (site, status), then the sort column with DESC baked
-- in, so Postgres reads it in output order with no sort step.
CREATE INDEX IF NOT EXISTS idx_assistant_questions_inbox
    ON assistant_questions (site_id, status, times_asked DESC, last_asked_at DESC);

-- INDEX: the tenant_id foreign key (postgres does not index FKs itself); it
-- also serves "delete a tenant" cascading without a table scan.
CREATE INDEX IF NOT EXISTS idx_assistant_questions_tenant
    ON assistant_questions (tenant_id);
