-- =============================================================================
--  024_template_vercel_project.sql — which Vercel project serves each template
-- =============================================================================
--  Run AFTER 023_notifications_ttl_index.sql.
--
--  Every merchant site now gets a clean `{subdomain}.{SITE_BASE_DOMAIN}` URL
--  with no template name in it (Aurora and Bazaar sites look identical at the
--  URL level). Since two Vercel projects can't share one wildcard domain,
--  each site's exact subdomain is instead attached directly to the correct
--  project via the Vercel API at publish time (see app/vercel.py) — this
--  column is what tells the publish flow which project that is, per
--  template. NULL until you fill it in with the real Vercel project id after
--  creating the Aurora/Bazaar projects.
-- =============================================================================

ALTER TABLE templates
    ADD COLUMN IF NOT EXISTS vercel_project_id text;
