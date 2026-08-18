-- Category banner image — separate from image_url (the small/profile-style
-- category thumbnail). The shop page swaps a wide banner image when the
-- visitor switches category filters; that needs its own real image per
-- category, not the same one used for thumbnails.

ALTER TABLE categories
    ADD COLUMN IF NOT EXISTS banner_url text;
