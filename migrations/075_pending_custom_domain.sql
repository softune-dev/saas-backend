-- =============================================================================
-- A custom domain only becomes a site's real domain once it is actually
-- connected. Until then it waits in pending_custom_domain.
--
-- Before this, PATCH /sites/{id} wrote whatever the merchant typed straight
-- into sites.custom_domain, and the whole platform trusts that column: the
-- storefront host, SEO links, the Vercel attach job and the provisioning
-- check all prefer custom_domain over the subdomain. So a typo, or a domain
-- the merchant never pointed at us, replaced a working address with a broken
-- one (a live example was the text "mehedi store" saved as a domain).
--
-- Now:
--   custom_domain          the domain that is connected and serving. Only ever
--                          set by app/domains.py once Vercel confirms the
--                          domain is attached and its DNS points at us.
--   pending_custom_domain  what the merchant asked for and is still setting
--                          up. Never used to serve anything. A background
--                          sweep promotes it when it connects, and drops it
--                          after 14 days.
--   pending_domain_requested_at  when it was requested (for that 14 day limit).
--
-- Existing rows are untouched: a site that already has custom_domain keeps it.
-- =============================================================================

ALTER TABLE sites ADD COLUMN IF NOT EXISTS pending_custom_domain citext;
ALTER TABLE sites ADD COLUMN IF NOT EXISTS pending_domain_requested_at timestamptz;

-- INDEX: the background sweep that promotes or expires pending domains reads
-- only the few rows that have one, so a partial index keeps it tiny.
CREATE INDEX IF NOT EXISTS idx_sites_pending_domain
    ON sites (pending_domain_requested_at)
    WHERE pending_custom_domain IS NOT NULL;
