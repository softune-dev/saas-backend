-- =============================================================================
-- order_items.image_url_snapshot: the product photo the buyer saw.
--
-- The dashboard's Orders table shows each order's product photos. Order
-- history is immutable (CLAUDE.md rule 8), so a past order must not show a
-- photo by looking up today's product: the photo is saved on the line when
-- the order is placed, like name_snapshot and sku_snapshot.
--
-- Existing rows are filled once from their product's current first image
-- (the best record there is for orders placed before this column existed).
-- Lines whose product was deleted stay NULL; the dashboard shows a plain
-- placeholder for those.
-- =============================================================================

ALTER TABLE order_items ADD COLUMN IF NOT EXISTS image_url_snapshot text;

UPDATE order_items oi
SET image_url_snapshot = p.images -> 0 ->> 'url'
FROM products p
WHERE oi.product_id = p.id
  AND oi.image_url_snapshot IS NULL
  AND jsonb_typeof(p.images) = 'array'
  AND jsonb_array_length(p.images) > 0;
