-- Optional icon for a category — a lucide-react icon name (e.g. "Truck"),
-- not an uploaded image. Only some storefront templates render a category
-- icon (e.g. Bazaar's department rail); templates that don't just ignore
-- the column. Nullable because most templates never read it.

ALTER TABLE categories
    ADD COLUMN IF NOT EXISTS icon text;
