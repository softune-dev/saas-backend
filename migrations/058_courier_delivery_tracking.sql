-- =============================================================================
-- Tracks a booked shipment on the order it belongs to, and adds the webhook
-- secret a courier calls back with. Closes the gap that made "delivery
-- success rate" unmeasurable: until now, app/steadfast.py only verified
-- credentials — nothing ever created a real consignment or recorded what
-- happened to a parcel after booking.
--
-- courier_consignment_id gets its own index (not a FK — it's an opaque id
-- from the courier's own system, not a row we own) because the webhook
-- receiver (app/api/public.py's steadfast_webhook) looks an order up BY this
-- value on every callback, with no other filter available at that point.
--
-- webhook_secret lives on courier_connections, not encrypted like
-- api_key_encrypted/secret_key_encrypted above it: this is a token WE mint
-- and must keep re-displaying to the merchant so they can paste it into
-- Steadfast's own panel, and it can only be used to forge a delivery-status
-- callback into our own webhook — not to touch the merchant's real Steadfast
-- account. Lower stakes than the credentials, different treatment.
-- =============================================================================

ALTER TABLE orders ADD COLUMN IF NOT EXISTS courier_provider text;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS courier_consignment_id text;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS courier_tracking_code text;
-- not_booked (default/implicit via NULL) | in_review | delivered | cancelled | ...
-- — see app/steadfast.py's STATUS_MAP for the exact values a webhook can set.
ALTER TABLE orders ADD COLUMN IF NOT EXISTS delivery_status text;

CREATE INDEX IF NOT EXISTS ix_orders_courier_consignment_id
    ON orders (courier_consignment_id)
    WHERE courier_consignment_id IS NOT NULL;

ALTER TABLE courier_connections ADD COLUMN IF NOT EXISTS webhook_secret text;
