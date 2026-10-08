-- =============================================================================
-- Admin audit log: one row per action a platform operator takes in
-- /superadmin (plan change, ban, delete, credit grant, payment approval,
-- password reset, campaign send...). Answers "who changed this tenant, and
-- when" without digging through server logs.
--
-- target_id is text, not a foreign key: the row has to survive the thing it
-- describes being deleted (deleting a tenant is itself an audited action), so
-- target_label keeps a human readable name of what it was.
-- actor_id SET NULL with actor_email kept as text, for the same reason.
-- =============================================================================

CREATE TABLE IF NOT EXISTS admin_audit_logs (
    id           uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    actor_id     uuid                 REFERENCES users(id) ON DELETE SET NULL,
    actor_email  text        NOT NULL,
    action       text        NOT NULL,
    target_type  text        NOT NULL,
    target_id    text,
    target_label text,
    details      jsonb       NOT NULL DEFAULT '{}'::jsonb,
    created_at   timestamptz NOT NULL DEFAULT now()
);

-- INDEX: the audit log page, newest first.
CREATE INDEX IF NOT EXISTS idx_admin_audit_created
    ON admin_audit_logs (created_at DESC);

-- INDEX: one tenant's (or user's) history, shown in its detail drawer.
CREATE INDEX IF NOT EXISTS idx_admin_audit_target
    ON admin_audit_logs (target_type, target_id, created_at DESC);

-- INDEX: the actor_id foreign key (ON DELETE SET NULL has to find these rows).
CREATE INDEX IF NOT EXISTS idx_admin_audit_actor
    ON admin_audit_logs (actor_id);
