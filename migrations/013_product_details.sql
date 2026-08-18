-- 013: richer product detail fields — SEO short description, a real (HTML)
-- long description, product video, inventory identifiers, and per-product
-- shipping override.
--
-- description already existed as plain text; it now holds sanitized HTML
-- from the dashboard's rich text editor instead of plain text. No migration
-- needed for that column itself — same column, richer content.
--
-- No new foreign keys here, so no new index (CLAUDE.md rule 6 only applies
-- to FKs).

ALTER TABLE products
    -- SEO meta-description-length snippet, separate from the long
    -- description so a storefront's <meta name="description"> and a search
    -- result snippet aren't stuck rendering a chunk of rich HTML.
    ADD COLUMN IF NOT EXISTS short_description text,

    -- Either an uploaded Cloudinary video URL or a pasted external link
    -- (YouTube/Vimeo/etc). One column, not two — the frontend can tell them
    -- apart by URL shape when deciding how to render/autoplay it.
    ADD COLUMN IF NOT EXISTS video_url text,

    -- Free-form manufacturer/lot serial — not the same as sku (this site's
    -- own catalog code), so kept separate rather than overloading sku.
    ADD COLUMN IF NOT EXISTS serial_number text,

    -- Stock unit label (kg, ml, l, mg, pcs, ...). Nullable — a garment
    -- product (sizes, not weights/volumes) leaves this unset.
    ADD COLUMN IF NOT EXISTS unit text,

    -- A merchant-entered "starting" sold count, added on top of real
    -- completed-order counts for social proof on a brand-new product.
    -- Deliberately NOT a real sales figure — see ProductOut's comment.
    ADD COLUMN IF NOT EXISTS initial_sold_count integer NOT NULL DEFAULT 0
        CHECK (initial_sold_count >= 0),

    -- true = free delivery (the common case, default). false = this product
    -- overrides the site's delivery charge with delivery_charge_cents.
    ADD COLUMN IF NOT EXISTS free_delivery boolean NOT NULL DEFAULT true,

    -- Only meaningful when free_delivery = false. Same integer-cents money
    -- rule as price_cents.
    ADD COLUMN IF NOT EXISTS delivery_charge_cents integer
        CHECK (delivery_charge_cents IS NULL OR delivery_charge_cents >= 0);
