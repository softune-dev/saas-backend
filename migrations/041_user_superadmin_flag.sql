-- =============================================================================
--  041_user_superadmin_flag.sql — platform-level admin flag
-- =============================================================================
--  A superadmin is a normal `users` row (still belongs to a tenant, to satisfy
--  the existing NOT NULL FK) with this flag set. It carries NO special
--  meaning to tenant-scoped queries anywhere in the app — every existing
--  crud.get_scoped/list_scoped call is completely unaffected by this column.
--  It is checked in exactly one place: app/security.py's require_superadmin
--  dependency, which gates the new /superadmin/* router (app/api/superadmin.py)
--  and nothing else. See that router's module docstring for why cross-tenant
--  SELECTs are legitimate there and nowhere else in the codebase.
-- =============================================================================

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS is_superadmin boolean NOT NULL DEFAULT false;
