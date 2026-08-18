-- 009: hero becomes images-only.
--
-- The hero used to carry five text fields (eyebrow, title, body, and two CTA
-- labels). Aurora now renders nothing but images there, so those keys are dead
-- weight in every stored theme — and leaving them behind is worse than untidy:
-- the editor round-trips whatever it loads, so stale keys would keep being
-- written back forever, and the next person reading a theme JSON would
-- reasonably assume the hero still shows text.
--
-- Replaces them with:
--   heroImages        text[] as JSONB array — 16:9, required, drives desktop
--   heroImagesSquare  text[] as JSONB array — 1:1, optional, mobile only
--
-- Existing sites get the template's default images rather than an empty array,
-- because an empty heroImages renders no hero at all and would look to the
-- merchant like their homepage lost a section.

-- ---------------------------------------------------------------------------
-- 1. Template defaults
-- ---------------------------------------------------------------------------

UPDATE templates
SET default_theme =
      (default_theme
        - 'heroEyebrow'
        - 'heroTitle'
        - 'heroBody'
        - 'heroCta'
        - 'heroSecondary')
      || jsonb_build_object(
           'heroImages',
           jsonb_build_array('/assets/hero-1.jpg', '/assets/hero-2.jpg'),
           'heroImagesSquare',
           '[]'::jsonb
         )
WHERE key = 'aurora';

-- ---------------------------------------------------------------------------
-- 2. Live sites — AURORA ONLY
-- ---------------------------------------------------------------------------
-- Scoped by template_id on purpose. Other templates (sweets) still render a
-- text hero from these exact keys, so an unscoped UPDATE here would silently
-- blank the headline on every one of their storefronts.
--
-- COALESCE keeps any hero images a site somehow already has; without it a
-- re-run of this migration would reset a merchant's chosen images back to the
-- defaults.

UPDATE sites
SET theme =
      (theme
        - 'heroEyebrow'
        - 'heroTitle'
        - 'heroBody'
        - 'heroCta'
        - 'heroSecondary')
      || jsonb_build_object(
           'heroImages',
           COALESCE(
             theme -> 'heroImages',
             jsonb_build_array('/assets/hero-1.jpg', '/assets/hero-2.jpg')
           ),
           'heroImagesSquare',
           COALESCE(theme -> 'heroImagesSquare', '[]'::jsonb)
         )
WHERE template_id IN (SELECT id FROM templates WHERE key = 'aurora')
  AND (
        theme ?| array['heroEyebrow', 'heroTitle', 'heroBody', 'heroCta', 'heroSecondary']
     OR NOT (theme ? 'heroImages')
      );

-- ---------------------------------------------------------------------------
-- 3. Nav links gain an explicit route
-- ---------------------------------------------------------------------------
-- Links were label-only, so the storefront sent every one of them to "/".
-- Anything without a path gets "/" explicitly — same behaviour as before, but
-- now visible and editable in the dashboard instead of implied by a fallback.
--
-- Safe for every template: this only ADDS a key that was already optional in
-- the type, so a template that ignores it is unaffected.

UPDATE sites
SET theme = jsonb_set(
      theme,
      '{navLinks}',
      (
        SELECT COALESCE(jsonb_agg(
                 CASE
                   WHEN link ? 'path' THEN link
                   ELSE link || jsonb_build_object('path', '/')
                 END
               ), '[]'::jsonb)
        FROM jsonb_array_elements(theme -> 'navLinks') AS link
      )
    )
WHERE jsonb_typeof(theme -> 'navLinks') = 'array';
