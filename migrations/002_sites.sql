-- =============================================================================
--  002_sites.sql — templates, sites, pages
-- =============================================================================
--  Run AFTER 001_core.sql, in the Supabase SQL Editor.
--
--  This is where the architecture we discussed lives:
--    templates  = the catalogue of designs you sell (code lives on Vercel)
--    sites      = one purchased instance, owned by a tenant
--    site_pages = the editable content, stored as a JSONB array of blocks
-- =============================================================================


-- =============================================================================
--  TEMPLATES — your product catalogue. Not tenant-owned; shared by everyone.
-- =============================================================================
CREATE TABLE IF NOT EXISTS templates (
    id            uuid        PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Stable machine key, e.g. 'restaurant-01'. This is the contract between
    -- this database and the actual template repo deployed on Vercel. Renaming a
    -- template's display name must never break a live site, so the code reads
    -- `key`, and `name` is free to change.
    key           citext      NOT NULL UNIQUE,

    name          text        NOT NULL,
    description   text,

    -- Which stack the template repo is built with. Drives one real behavioural
    -- difference: 'nextjs' sites get an on-demand revalidation call when content
    -- changes; 'vite' sites fetch config client-side and need no such call.
    framework     text        NOT NULL DEFAULT 'nextjs'
                              CHECK (framework IN ('nextjs', 'vite')),

    -- The template's MANIFEST: which block types it knows how to render, in
    -- default order. The admin panel intersects this with the block registry
    -- (app/blocks.py) to decide which "Add section" options to offer.
    -- text[] not jsonb: it is a flat list of short strings, and Postgres arrays
    -- give us the `&&` overlap operator plus a smaller GIN index.
    block_types   text[]      NOT NULL DEFAULT '{}',

    -- Starting values a new site is seeded with, so a freshly purchased site
    -- looks finished instead of empty. Shape matches sites.theme / site_pages.
    default_theme jsonb       NOT NULL DEFAULT '{}'::jsonb,
    default_pages jsonb       NOT NULL DEFAULT '[]'::jsonb,

    preview_url   text,
    thumbnail_url text,
    price_cents   integer     NOT NULL DEFAULT 0 CHECK (price_cents >= 0),
    is_active     boolean     NOT NULL DEFAULT true,

    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

-- INDEX: the storefront listing ("show me templates I can buy").
-- Partial + covering: only active rows are ever listed, and INCLUDE carries the
-- display columns inside the index so the planner can answer the whole query
-- from the index alone (an "index-only scan") without touching the table heap.
CREATE INDEX IF NOT EXISTS idx_templates_active
    ON templates (name)
    INCLUDE (key, framework, thumbnail_url, price_cents)
    WHERE is_active;

-- INDEX: "which templates support a Pricing block?" — GIN enables the array
-- containment operators (@>, &&) to use an index instead of scanning.
CREATE INDEX IF NOT EXISTS idx_templates_block_types
    ON templates USING gin (block_types);

DROP TRIGGER IF EXISTS trg_templates_updated_at ON templates;
CREATE TRIGGER trg_templates_updated_at
    BEFORE UPDATE ON templates
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();


-- =============================================================================
--  SITES — one purchased, editable site instance
-- =============================================================================
CREATE TABLE IF NOT EXISTS sites (
    id            uuid        PRIMARY KEY DEFAULT gen_random_uuid(),

    tenant_id     uuid        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,

    -- ON DELETE RESTRICT, deliberately different from the cascade above:
    -- you must not be able to delete a template that live customer sites depend
    -- on. Postgres will refuse and you'll retire the template with
    -- is_active=false instead — which is the correct business behaviour.
    template_id   uuid        NOT NULL REFERENCES templates(id) ON DELETE RESTRICT,

    name          text        NOT NULL,

    -- The hosted address: <subdomain>.vercel.app today, <subdomain>.yourdomain.com
    -- once you point your own domain. Globally unique because it is a hostname.
    subdomain     citext      NOT NULL UNIQUE,

    -- A customer's own domain, once they attach one. NULL until then.
    custom_domain citext,

    status        text        NOT NULL DEFAULT 'draft'
                              CHECK (status IN ('draft', 'published', 'suspended')),

    -- ---- The three JSONB config columns -----------------------------------
    -- Deliberately schema-less, for the reason we worked through: every site
    -- needs a different set of sections, and forcing that into rigid columns
    -- means a migration every time one customer wants something another doesn't.
    -- Shape is validated in Python (app/schemas.py + app/blocks.py) BEFORE it
    -- reaches the database, so "flexible storage" does not mean "unvalidated".

    -- Design tokens: colors, fonts, radius, spacing scale.
    theme         jsonb       NOT NULL DEFAULT '{}'::jsonb,

    -- Business details entered once at signup: legal name, address, phone,
    -- opening hours, social links. This is the source that auto-generates
    -- JSON-LD structured data, so the customer never fills an SEO form twice.
    business      jsonb       NOT NULL DEFAULT '{}'::jsonb,

    -- Site-wide SEO defaults: default title suffix, default OG image, favicon,
    -- and a `noindex` kill switch used while a site is still a draft.
    seo           jsonb       NOT NULL DEFAULT '{}'::jsonb,

    published_at  timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

-- INDEX: the admin panel's main list — "my sites", newest first.
-- Column order matters: tenant_id is the equality filter so it goes FIRST;
-- created_at is the sort so it goes SECOND, with DESC baked in so Postgres can
-- walk the index in output order and skip the sort step entirely.
CREATE INDEX IF NOT EXISTS idx_sites_tenant_created
    ON sites (tenant_id, created_at DESC);

-- INDEX: foreign key to templates (again, Postgres won't do this for you).
-- Also answers "how many live sites use this template?" before you retire it.
CREATE INDEX IF NOT EXISTS idx_sites_template ON sites (template_id);

-- UNIQUE INDEX: custom domain, but only for rows that have one.
-- WHY PARTIAL: a plain UNIQUE would treat every NULL as distinct (so it would
-- technically work), but a partial index excludes the many NULL rows from the
-- index altogether — smaller, faster, and it documents the intent.
CREATE UNIQUE INDEX IF NOT EXISTS uq_sites_custom_domain
    ON sites (custom_domain)
    WHERE custom_domain IS NOT NULL;

-- INDEX: THE HOTTEST QUERY IN THE WHOLE SYSTEM.
-- Every visitor to every customer site triggers "resolve this hostname to a
-- site config". It must be a single index hit. Partial on published-only keeps
-- drafts out of the index, and INCLUDE lets the lookup return the config
-- columns without a heap fetch.
-- (Redis caches this on top — see app/cache.py — but the cold path still has to
--  be fast, because a cache miss happens on every deploy and every edit.)
CREATE INDEX IF NOT EXISTS idx_sites_published_subdomain
    ON sites (subdomain)
    INCLUDE (id, tenant_id, template_id, theme, business, seo)
    WHERE status = 'published';

DROP TRIGGER IF EXISTS trg_sites_updated_at ON sites;
CREATE TRIGGER trg_sites_updated_at
    BEFORE UPDATE ON sites
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();


-- =============================================================================
--  SITE_PAGES — the editable content
-- =============================================================================
CREATE TABLE IF NOT EXISTS site_pages (
    id           uuid        PRIMARY KEY DEFAULT gen_random_uuid(),

    site_id      uuid        NOT NULL REFERENCES sites(id) ON DELETE CASCADE,

    -- DENORMALISED on purpose. It is derivable via sites.tenant_id, but storing
    -- it here means every ownership check and every tenant-scoped list is a
    -- single-table index scan with no JOIN. Two concrete wins:
    --   1. Security: the isolation filter is one WHERE clause on the table you
    --      are already touching. No chance of forgetting the join and leaking.
    --   2. Speed: no join to the parent on the hot read path.
    -- The cost is that a page must never be re-parented to another tenant's
    -- site. Nothing in the API does that, and the FK pair below makes it hard.
    tenant_id    uuid        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,

    -- URL path segment. '' (empty string) is the homepage, 'about' is /about.
    slug         citext      NOT NULL,

    title        text        NOT NULL,

    -- THE BLOCK ARRAY — the heart of the editor.
    -- Shape: [ { "type": "Hero", "id": "...", "data": { ... } }, ... ]
    -- Order in the array IS the render order on the page, so reordering
    -- sections is just reordering this array — no sort_order column to maintain
    -- across siblings.
    blocks       jsonb       NOT NULL DEFAULT '[]'::jsonb,

    -- Per-page SEO overrides: title, meta_description, og_image, noindex.
    -- Anything absent falls back to sites.seo, resolved in app/api/public.py.
    seo          jsonb       NOT NULL DEFAULT '{}'::jsonb,

    is_published boolean     NOT NULL DEFAULT false,

    -- Navigation order only (the nav menu), independent of blocks order.
    sort_order   integer     NOT NULL DEFAULT 0,

    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),

    -- One page per path per site. The database enforces it so a double-clicked
    -- "Add page" button cannot create two /about pages.
    CONSTRAINT uq_site_pages_site_slug UNIQUE (site_id, slug)
);

-- INDEX: render a specific page of a specific site — the public read path.
-- Partial on published so unpublished drafts never bloat the hot index.
-- (uq_site_pages_site_slug above could serve this, but it includes drafts; this
--  one is smaller and is what the public endpoint will actually pick.)
CREATE INDEX IF NOT EXISTS idx_site_pages_published
    ON site_pages (site_id, slug)
    WHERE is_published;

-- INDEX: nav menu / page list for one site, already in display order.
CREATE INDEX IF NOT EXISTS idx_site_pages_site_order
    ON site_pages (site_id, sort_order, slug);

-- INDEX: the tenant isolation filter.
CREATE INDEX IF NOT EXISTS idx_site_pages_tenant ON site_pages (tenant_id);

-- INDEX: GIN on the block array.
-- WHY: enables questions you WILL eventually ask, such as "which pages still use
-- the old Hero block?" before you change its schema, via
--   WHERE blocks @> '[{"type": "Hero"}]'
-- jsonb_path_ops is a smaller, faster GIN variant that supports exactly the
-- containment operator (@>) we need — we don't need key-existence queries.
CREATE INDEX IF NOT EXISTS idx_site_pages_blocks
    ON site_pages USING gin (blocks jsonb_path_ops);

DROP TRIGGER IF EXISTS trg_site_pages_updated_at ON site_pages;
CREATE TRIGGER trg_site_pages_updated_at
    BEFORE UPDATE ON site_pages
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
