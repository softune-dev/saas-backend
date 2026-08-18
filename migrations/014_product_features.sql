-- 014: product feature highlights — merchant-entered {title, description}
-- pairs shown as icon callouts on the storefront product page (was three
-- hardcoded "Maison Commitments" blocks in the Aurora template with no
-- backing field at all).
--
-- JSONB, not a table: same reasoning as products.images — small, ordered,
-- always read/written as a whole list alongside the product, never queried
-- independently. No new foreign key, so no new index (CLAUDE.md rule 6).

ALTER TABLE products
    ADD COLUMN IF NOT EXISTS features jsonb NOT NULL DEFAULT '[]'::jsonb;
