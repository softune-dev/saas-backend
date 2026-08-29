-- Real visitor/traffic data — the one thing app/api/analytics.py's own
-- docstring said didn't exist yet ("no traffic/session tracking exists").
-- One row per storefront page load, fired best-effort from the client (see
-- app/api/public.py's log_page_view) — never blocks a page from rendering
-- if the beacon call fails or is slow.
--
-- session_id is a random id the storefront generates once and keeps in
-- localStorage — not a real identity, just enough to count "unique
-- visitors" and compute revenue/visits) without any cookie-consent-grade
-- tracking or PII.
create table if not exists page_views (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references tenants(id) on delete cascade,
  site_id uuid not null references sites(id) on delete cascade,
  path text not null,
  referrer text,
  session_id text not null,
  created_at timestamptz not null default now()
);

-- Required on every FK per project convention, even though queries here go
-- through site_id, not tenant_id, directly.
create index if not exists ix_page_views_tenant_id on page_views(tenant_id);
-- Every list/count query here filters by (site_id, created_at) for a
-- window — the two columns analytics.py's queries actually use together.
create index if not exists ix_page_views_site_created on page_views(site_id, created_at);
-- Distinct-session counting for a window groups by session_id within a
-- site — this index backs that without a full table scan.
create index if not exists ix_page_views_site_session on page_views(site_id, session_id);
