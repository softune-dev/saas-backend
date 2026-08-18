-- 010: font pickers replace the dead fontFamily field; headerStyle removed.
--
-- fontFamily was never read by any Aurora component — same pattern as the
-- old hero text fields and headerStyle below. It's replaced by two fields
-- that ARE wired up: displayFont (headings) and bodyFont (body text), each a
-- key into a small curated set of next/font families actually loaded in
-- templates/aurora/app/layout.tsx. See theme-context.tsx's DISPLAY_FONTS /
-- BODY_FONTS for the exact keys — this migration's defaults must match them.
--
-- headerStyle (Light/Solid/Minimal) was ALSO never read anywhere — Header.tsx
-- has one fixed look. Dropped outright rather than replaced.
--
-- Scoped to Aurora only, same reasoning as migration 009: other templates
-- (sweets) may still read fontFamily/headerStyle from their own theme shape,
-- and an unscoped UPDATE would silently change their stored data too.

UPDATE templates
SET default_theme =
      (default_theme - 'fontFamily' - 'headerStyle')
      || jsonb_build_object('displayFont', 'fraunces', 'bodyFont', 'inter')
WHERE key = 'aurora';

UPDATE sites
SET theme =
      (theme - 'fontFamily' - 'headerStyle')
      || jsonb_build_object(
           'displayFont', COALESCE(theme ->> 'displayFont', 'fraunces'),
           'bodyFont', COALESCE(theme ->> 'bodyFont', 'inter')
         )
WHERE template_id IN (SELECT id FROM templates WHERE key = 'aurora')
  AND (
        theme ?| array['fontFamily', 'headerStyle']
     OR NOT (theme ? 'displayFont')
     OR NOT (theme ? 'bodyFont')
      );
