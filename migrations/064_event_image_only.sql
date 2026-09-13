-- =============================================================================
--  064_event_image_only.sql — "image only" display mode for events
-- =============================================================================
--  Run AFTER 063_fashion_classic_template.sql.
--
--  Lets a merchant use a pre-designed promo banner image as the whole event —
--  title/description/CTA baked into the artwork — instead of the storefront
--  always layering its own text and button over the image. Applies wherever
--  an event renders (homepage Events section card, popup modal): when true,
--  only the image shows.
--
--  No uniqueness/exclusivity here unlike is_popup (062) — any number of
--  events can be image_only independently of each other and of is_popup.
-- =============================================================================

ALTER TABLE events
    ADD COLUMN IF NOT EXISTS image_only boolean NOT NULL DEFAULT false;
