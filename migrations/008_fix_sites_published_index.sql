-- =============================================================================
--  008_fix_sites_published_index.sql — drop the oversized covering index
-- =============================================================================
--  Run AFTER 007_aurora_template.sql. Safe to re-run (DROP IF EXISTS / CREATE
--  IF NOT EXISTS).
--
--  THE BUG
--  idx_sites_published_subdomain (see migrations/002_sites.sql) was created
--  with `INCLUDE (id, tenant_id, template_id, theme, business, seo)` — an
--  attempt at an index-only scan for the public site lookup. It worked while
--  every seeded template's `theme` blob was tiny (~100 bytes: colors, fonts,
--  radius). It breaks the instant a real "contract family" template (Aurora,
--  Sweets — see 007's header for what that means) is published, because their
--  `theme` column holds the FULL SiteEditorSettings object: nav links, every
--  page, every section, testimonials — routinely several KB. Postgres btree
--  INCLUDE columns have a hard per-row limit (~1/3 of an 8KB page, ≈2704
--  bytes here). Publishing any real Aurora/Sweets site hits:
--
--    index row size 2824 exceeds btree version 4 maximum 2704
--
--  WHY THE FIX IS "REMOVE THE INCLUDE", NOT "MAKE THE INDEX BIGGER"
--  The INCLUDE was never actually earning its keep for the query it exists to
--  serve. _find_published_site() in app/api/public.py runs `select(Site)` —
--  every mapped column, not a narrow projection — so Postgres could not have
--  satisfied it as an index-only scan regardless of what was included; a heap
--  fetch was always happening. Meanwhile GET /public/site/{host} is cached in
--  Redis on top (app/cache.py) specifically so this cold-path query is rare.
--  A plain index on the lookup column, sized safely, is the correct shape.
-- =============================================================================

DROP INDEX IF EXISTS idx_sites_published_subdomain;

-- Same lookup column, same partial condition, no INCLUDE. Comment states the
-- query it serves, per this project's indexing convention.
CREATE INDEX IF NOT EXISTS idx_sites_published_subdomain
    ON sites (subdomain)
    WHERE status = 'published';
