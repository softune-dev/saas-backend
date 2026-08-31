-- =============================================================================
--  046_rls_new_tables.sql — close the Supabase auto-REST exposure gap
-- =============================================================================
--  This backend's real access control is app-layer, not RLS (see CLAUDE.md
--  rule 1 — every tenant-owned query goes through app/crud.py's
--  get_scoped/list_scoped, and the backend connects as the `postgres` role,
--  which bypasses RLS regardless of what's enabled here). But Supabase
--  auto-exposes every public table via PostgREST to its `anon`/
--  `authenticated` roles UNLESS RLS is enabled — a table with RLS on and
--  zero policies defaults to deny-all for those roles, which is exactly
--  what these three tables need: nothing here should ever be reachable
--  through Supabase's REST API, only through this backend's own
--  `postgres`-role connection.
--
--  Deliberately NOT "FORCE ROW LEVEL SECURITY" — that would also restrict
--  the table owner, breaking the backend's own access. Plain ENABLE is
--  correct: owner/superuser (postgres) is exempt by default, only
--  anon/authenticated get walled off.
-- =============================================================================

ALTER TABLE leads ENABLE ROW LEVEL SECURITY;
ALTER TABLE trusted_devices ENABLE ROW LEVEL SECURITY;
ALTER TABLE help_ticket_replies ENABLE ROW LEVEL SECURITY;
