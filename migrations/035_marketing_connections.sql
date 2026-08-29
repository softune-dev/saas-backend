-- A site's own marketing/tracking integration credentials. Currently just
-- Meta Conversions API (server-side Purchase events) — the client-side
-- pixel IDs (Meta Pixel, TikTok Pixel, GTM container) live in sites.seo
-- instead, since they're not secrets: they're visible in every page's HTML
-- source anyway. Only the CAPI access token needs encryption, same trust
-- boundary and same Fernet key as courier/payment credentials (see
-- app/courier_crypto.py) — no reason to manage a third key for the same
-- sensitivity level.
create table if not exists marketing_connections (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references tenants(id) on delete cascade,
  site_id uuid not null references sites(id) on delete cascade,
  provider text not null,
  status text not null default 'connected',
  access_token_encrypted text not null,
  access_token_hint text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint uq_marketing_connections_site_provider unique (site_id, provider)
);

-- Every list_scoped(MarketingConnection, tenant_id) call filters on this column.
create index if not exists ix_marketing_connections_tenant_id on marketing_connections(tenant_id);
-- The connect/disconnect/list endpoints all filter on site_id alone.
create index if not exists ix_marketing_connections_site_id on marketing_connections(site_id);

drop trigger if exists trg_marketing_connections_updated_at on marketing_connections;
create trigger trg_marketing_connections_updated_at
    before update on marketing_connections
    for each row execute function set_updated_at();
