-- =============================================================================
--  019_notifications.sql — dashboard bell notifications
-- =============================================================================
--  Run AFTER 018_category_icon.sql.
--
--  Backs the header notification bell (dashboard/components/layout/header/
--  action-icons-pill.tsx), previously mock data. Site-scoped (like everything
--  else here) since the dashboard always operates against one currentSite at
--  a time — a tenant with multiple sites gets separate notification streams
--  per site, matching how Orders/Analytics/etc already work.
--
--  Rows are created as a side effect of other writes (a new order, a publish/
--  unpublish) — see app/notifications.py's notify(). Never written directly
--  by the client; the API only reads and marks read.
--
--  No updated_at/trigger: the only mutation is read_at going from null to a
--  timestamp, which the API sets directly — a generic "touched at" column
--  would just duplicate that.
-- =============================================================================

CREATE TABLE IF NOT EXISTS notifications (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    site_id     uuid        NOT NULL REFERENCES sites(id)   ON DELETE CASCADE,

    type        text        NOT NULL
                             CHECK (type IN ('order_created', 'site_published', 'site_unpublished')),

    title       text        NOT NULL,
    body        text        NOT NULL DEFAULT '',
    -- Relative dashboard path the notification should navigate to on click,
    -- e.g. "/orders?highlight=<id>". Null when there's nowhere useful to go.
    link        text,

    read_at     timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- INDEX: the bell dropdown's exact query — latest N for one site, newest
-- first. Also serves the unread-count check (read_at IS NULL scan is cheap
-- once narrowed to one site via this index).
CREATE INDEX IF NOT EXISTS idx_notifications_site_created
    ON notifications (site_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_notifications_tenant
    ON notifications (tenant_id);
