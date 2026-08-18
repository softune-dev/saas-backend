-- =============================================================================
--  023_notifications_ttl_index.sql — index for the 15-day retention sweep
-- =============================================================================
--  Run AFTER 022_order_counters.sql.
--
--  app/worker.py now runs a periodic cleanup that deletes notifications
--  older than 15 days, across all sites (see cleanup_old_notifications()).
--  idx_notifications_site_created (from 019_notifications.sql) leads with
--  site_id, which doesn't help a global "created_at < cutoff" sweep — this
--  index does.
-- =============================================================================

CREATE INDEX IF NOT EXISTS idx_notifications_created_at
    ON notifications (created_at);
