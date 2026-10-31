-- =============================================================================
-- sites.theme_rev: a counter that goes up every time sites.theme changes.
--
-- Why it exists: the theme editor keeps its working copy in each browser's
-- localStorage. A merchant who edited on their phone and then published from
-- a laptop holding an older copy had the laptop's copy replace the whole
-- theme — their newer Why Choose Us image, testimonial screenshots and Banner
-- CTA text were wiped (incident on elarabd.store).
--
-- The dashboard now reads theme_rev together with the theme, merges only the
-- fields the merchant actually edited onto that theme, and sends the rev back
-- with the write. PATCH /sites/{id} rejects the write with 409 if the rev has
-- moved since (another device or the AI assistant saved in between), so a
-- stale copy can never silently overwrite a newer one.
--
-- Bumped by a trigger rather than in Python: the theme is written from
-- several places (the editor, AI store setup, the AI assistant and its Undo,
-- template switching), and a trigger covers every one of them, including any
-- added later, with no way to forget.
--
-- No index: the column is only ever read alongside the row it lives on.
-- =============================================================================

ALTER TABLE sites ADD COLUMN IF NOT EXISTS theme_rev integer NOT NULL DEFAULT 0;

CREATE OR REPLACE FUNCTION bump_site_theme_rev() RETURNS trigger AS $$
BEGIN
    -- IS DISTINCT FROM, not <>: a save that writes back an identical theme
    -- (or touches only seo/business) must not look like a conflicting edit.
    IF NEW.theme IS DISTINCT FROM OLD.theme THEN
        NEW.theme_rev := OLD.theme_rev + 1;
    ELSE
        -- Nobody sets theme_rev directly; keep it owned by this trigger.
        NEW.theme_rev := OLD.theme_rev;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_sites_theme_rev ON sites;
CREATE TRIGGER trg_sites_theme_rev
    BEFORE UPDATE ON sites
    FOR EACH ROW EXECUTE FUNCTION bump_site_theme_rev();
