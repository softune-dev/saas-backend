-- =============================================================================
--  054_rls_invoices.sql — close the Supabase auto-REST exposure gap for the
--  invoice tables added in 053, same reasoning as 046_rls_new_tables.sql.
-- =============================================================================
--  This backend's real access control is app-layer, not RLS (CLAUDE.md rule
--  1 — every tenant-owned query goes through app/crud.py's get_scoped/
--  list_scoped, and the backend connects as the `postgres` role, which
--  bypasses RLS regardless of what's enabled here). Supabase auto-exposes
--  every public table via PostgREST to its `anon`/`authenticated` roles
--  UNLESS RLS is enabled — a table with RLS on and zero policies defaults to
--  deny-all for those roles, which is exactly what invoices/invoice_counters
--  need: they carry billing amounts and tenant business snapshots, and
--  should only ever be reachable through this backend's own `postgres`-role
--  connection, never Supabase's REST API directly.
--
--  Deliberately NOT "FORCE ROW LEVEL SECURITY" — that would also restrict
--  the table owner, breaking the backend's own access. Plain ENABLE is
--  correct: owner/superuser (postgres) is exempt by default, only
--  anon/authenticated get walled off.
-- =============================================================================

ALTER TABLE invoices ENABLE ROW LEVEL SECURITY;
ALTER TABLE invoice_counters ENABLE ROW LEVEL SECURITY;
