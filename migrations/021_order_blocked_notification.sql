-- =============================================================================
--  021_order_blocked_notification.sql — new notification type: order_blocked
-- =============================================================================
--  Run AFTER 020_push_subscriptions.sql.
--
--  Fraud blocklist enforcement (app/api/public.py's create_public_order) now
--  actually rejects checkout for a blocklisted phone, and tells the merchant
--  it happened — this is the notification type that carries that message.
--  Widening the CHECK constraint that 019_notifications.sql created; Postgres
--  has no ALTER CHECK, so drop and recreate it with the new value included.
-- =============================================================================

ALTER TABLE notifications
    DROP CONSTRAINT IF EXISTS notifications_type_check;

ALTER TABLE notifications
    ADD CONSTRAINT notifications_type_check
    CHECK (type IN ('order_created', 'order_blocked', 'site_published', 'site_unpublished'));
