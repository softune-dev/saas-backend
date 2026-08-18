-- =============================================================================
--  022_order_counters.sql — O(1) per-site order numbering
-- =============================================================================
--  Run AFTER 021_order_blocked_notification.sql.
--
--  Replaces app/crud.py's next_order_number(), which used to run
--  `SELECT COUNT(*) FROM orders WHERE site_id = X` on every single checkout.
--  That scans every existing order row for the site each time — gets slower
--  as a site accumulates orders, and isn't even race-free under concurrent
--  checkouts (two simultaneous orders could compute the same count).
--
--  One row per site, incremented atomically via UPDATE ... RETURNING —
--  O(1) regardless of order history size, and race-free (the UPDATE takes a
--  row lock).
--
--  Backfill: seed every existing site's counter at 1000 + however many
--  orders it already has, so numbering continues where it left off instead
--  of colliding with real ORD-#### values already in use.
-- =============================================================================

CREATE TABLE IF NOT EXISTS order_counters (
    site_id     uuid    PRIMARY KEY REFERENCES sites(id) ON DELETE CASCADE,
    next_number integer NOT NULL DEFAULT 1000
);

INSERT INTO order_counters (site_id, next_number)
SELECT s.id, 1000 + COALESCE(o.cnt, 0)
FROM sites s
LEFT JOIN (
    SELECT site_id, COUNT(*) AS cnt FROM orders GROUP BY site_id
) o ON o.site_id = s.id
ON CONFLICT (site_id) DO NOTHING;
