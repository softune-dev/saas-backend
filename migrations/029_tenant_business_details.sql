-- Legal/tax identity for billing & invoicing (Account -> Business details).
-- Distinct from sites.business, which is customer-facing storefront contact info.
alter table tenants
  add column if not exists business jsonb not null default '{}'::jsonb;
