-- =============================================================================
-- AI agent changes: one row per change the AI assistant made to a store, so
-- the merchant can Undo it.
--
-- The assistant now applies design and content edits straight away instead
-- of proposing them (merchants kept asking it to "just do it"). The price of
-- acting instantly is a reliable way back: before every change the agent
-- stores exactly what it is about to overwrite in `undo`, and
-- POST /ai/changes/{id}/undo puts it back.
--
-- undo shapes (written and read only by app/ai_agent_tools.py):
--   {"type": "theme",          "before": {<theme key>: <old value>, ...},
--                              "after":  {<theme key>: <new value>, ...}}
--   {"type": "delete_product",  "product_id":  "<uuid>"}
--   {"type": "delete_category", "category_id": "<uuid>"}
--   {"type": "delete_event",    "event_id":    "<uuid>"}
--   {"type": "restore_product", "product_id": "<uuid>", "before": {<field>: <old>}}
--
-- Rows cascade with the tenant/site: an undo record is meaningless once the
-- store it would restore is gone.
-- =============================================================================

CREATE TABLE IF NOT EXISTS ai_agent_changes (
    id         uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id  uuid        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    site_id    uuid        NOT NULL REFERENCES sites(id)   ON DELETE CASCADE,
    tool       text        NOT NULL,
    summary    text        NOT NULL,
    undo       jsonb       NOT NULL DEFAULT '{}'::jsonb,
    undone_at  timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- Serves the undo endpoint's tenant-scoped lookup (crud.get_scoped filters on
-- tenant_id) and "latest changes for this store" listings, newest first.
CREATE INDEX IF NOT EXISTS ix_ai_agent_changes_tenant_created
    ON ai_agent_changes (tenant_id, created_at DESC);

-- Foreign-key index (CLAUDE.md rule 6): lets a site delete cascade without a
-- sequential scan of this table.
CREATE INDEX IF NOT EXISTS ix_ai_agent_changes_site
    ON ai_agent_changes (site_id);
