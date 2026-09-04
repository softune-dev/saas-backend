-- =============================================================================
-- Per-site courier automation settings — same shape/pattern as
-- sites.fraud_rules (migrations/009): {rule_id: {enabled, ...}}. First rule
-- is auto_book: when enabled, a newly placed storefront order is booked
-- with the site's connected Steadfast account automatically (see
-- app/courier_booking.py and app/worker.py's handle_book_courier), instead
-- of a merchant booking each order by hand from the dashboard.
-- =============================================================================

ALTER TABLE sites ADD COLUMN IF NOT EXISTS courier_rules jsonb NOT NULL DEFAULT '{}'::jsonb;
