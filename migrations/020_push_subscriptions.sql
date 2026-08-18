-- =============================================================================
--  020_push_subscriptions.sql — browser push subscriptions
-- =============================================================================
--  Run AFTER 019_notifications.sql.
--
--  One row per (browser tab's service worker, site). Lets app/push.py send a
--  real OS-level push notification for a new order even when the dashboard
--  tab is closed — see dashboard/lib/push.ts for how a subscription gets
--  created (PushManager.subscribe(), then POSTed here).
--
--  Site-scoped like notifications, not tenant-wide: a merchant only wants
--  "new order" pushes for the site they're actively watching, not every
--  site they own firing at once.
-- =============================================================================

CREATE TABLE IF NOT EXISTS push_subscriptions (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    site_id     uuid        NOT NULL REFERENCES sites(id)   ON DELETE CASCADE,

    -- The push service URL the browser assigned this subscription (unique
    -- per browser+device). Re-subscribing the same browser upserts by this.
    endpoint    text        NOT NULL,
    p256dh      text        NOT NULL,
    auth        text        NOT NULL,

    created_at  timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT uq_push_subscriptions_endpoint UNIQUE (endpoint)
);

CREATE INDEX IF NOT EXISTS idx_push_subscriptions_site
    ON push_subscriptions (site_id);
CREATE INDEX IF NOT EXISTS idx_push_subscriptions_tenant
    ON push_subscriptions (tenant_id);
