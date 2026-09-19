-- =============================================================================
-- Adds order_items.variant_snapshot — the ONE real relational-schema change
-- needed to support per-combination variant price/compare-at/stock/image
-- (see app/products.py's module docstring for the full "combinations"
-- shape, which otherwise lives entirely inside products.attributes JSONB
-- and needs no migration of its own).
--
-- A past order has to keep showing exactly which variant combination
-- (e.g. "Color: Navy, Size: M") was actually bought, even after the
-- product's combinations are later edited or removed — same immutable-
-- history reasoning as every other *_snapshot column on this table
-- (CLAUDE.md rule 8). Null for a product with no variants.
-- =============================================================================

ALTER TABLE order_items ADD COLUMN IF NOT EXISTS variant_snapshot text;
