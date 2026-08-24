-- =============================================================================
--  033_indexing_pass.sql — close the two real indexing gaps found in a full
--  foreign-key audit (every FK across the schema checked against pg_index
--  for a covering index; these were the only two app tables missing one).
--  Run AFTER 032_customers.sql.
-- =============================================================================

-- help_tickets.user_id had no index at all — a real, uncovered foreign key.
-- Matters for: any "this user's tickets" query (none exist yet, but the FK
-- itself needs it), and for DELETE FROM users cascading into help_tickets —
-- without an index, Postgres has to sequential-scan help_tickets to find
-- matching rows on every user deletion.
create index if not exists ix_help_tickets_user_id on help_tickets(user_id);

-- Real query (app/api/help_desk.py): filter tenant_id, order by
-- created_at desc. The existing plain ix_help_tickets_tenant_id index can't
-- satisfy the sort without a separate sort step once a tenant has more than
-- a handful of tickets — same composite pattern already used for
-- orders/products elsewhere in this schema.
create index if not exists ix_help_tickets_tenant_created
    on help_tickets(tenant_id, created_at desc);

-- Real query (app/api/customers.py): filter site_id, order by created_at
-- desc. ix_customers_site_id (plain) can't satisfy the sort order either —
-- same fix as help_tickets above.
create index if not exists ix_customers_site_created
    on customers(site_id, created_at desc);
