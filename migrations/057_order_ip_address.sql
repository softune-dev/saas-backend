-- =============================================================================
-- Captures the requesting IP on every public checkout order — without this,
-- a merchant had no way to discover WHICH IP to type into the fraud IP
-- blocklist (Settings -> Fraud Protection), since nothing in the dashboard
-- surfaced a visitor's IP anywhere. See app/api/public.py's
-- create_public_order and dashboard/components/orders/order-detail-modal.tsx's
-- new "Block this IP" action.
--
-- Nullable, same as device_id (migrations/056): POS orders and any order
-- placed before this shipped have none.
-- =============================================================================

ALTER TABLE orders ADD COLUMN IF NOT EXISTS ip_address inet;
