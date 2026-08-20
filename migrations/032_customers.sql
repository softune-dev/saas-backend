-- Real customer records for repeat-buyer tracking. Until now Order.customer
-- was a disconnected JSONB blob per order, so there was no way to tell a
-- one-time buyer from someone's fifth order. Site-scoped, not tenant-scoped
-- across all of a merchant's stores — a phone number belongs to a specific
-- storefront's customer base, matching how orders are already queried
-- per-site everywhere else.
--
-- Phone is the dedup key, not email: these are COD-heavy stores where a
-- phone number is collected on every order and email frequently isn't.
create table if not exists customers (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references tenants(id) on delete cascade,
  site_id uuid not null references sites(id) on delete cascade,
  phone text not null,
  name text,
  email text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint uq_customers_site_phone unique (site_id, phone)
);

-- Every list_scoped(Customer, tenant_id) call filters on this column.
create index if not exists ix_customers_tenant_id on customers(tenant_id);
-- Every customer lookup during order creation filters on (site_id, phone) —
-- already covered by the unique constraint's implicit index, but the
-- customer detail page's "list this site's customers" view filters on
-- site_id alone, so it needs its own index.
create index if not exists ix_customers_site_id on customers(site_id);

drop trigger if exists trg_customers_updated_at on customers;
create trigger trg_customers_updated_at
    before update on customers
    for each row execute function set_updated_at();

-- Existing orders keep their JSONB customer blob exactly as-is — order
-- history is immutable (see CLAUDE.md rule 8), so this is never backfilled.
-- Only orders created from here on get linked to a real customer record.
alter table orders add column if not exists customer_id uuid references customers(id) on delete set null;

-- Every "this customer's order history" query filters on this column.
create index if not exists ix_orders_customer_id on orders(customer_id);
