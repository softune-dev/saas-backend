-- Support tickets for the Help Desk page. Tenant-scoped (not site-scoped —
-- an account may have multiple sites, but support is billed/handled at the
-- account level). No admin/agent reply flow yet (soft launch) — merchants
-- see their own tickets and their status; a human replies out of band
-- (email) for now, same as the "we'll reply by email" copy already on the
-- ticket form.
create table if not exists help_tickets (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references tenants(id) on delete cascade,
  user_id uuid not null references users(id) on delete cascade,
  subject text not null,
  category text not null,
  priority text not null default 'Medium',
  status text not null default 'Open',
  message text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Every list_scoped(HelpTicket, tenant_id) call filters on this column.
create index if not exists ix_help_tickets_tenant_id on help_tickets(tenant_id);

drop trigger if exists trg_help_tickets_updated_at on help_tickets;
create trigger trg_help_tickets_updated_at
    before update on help_tickets
    for each row execute function set_updated_at();
