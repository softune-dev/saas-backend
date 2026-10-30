-- =============================================================================
-- Allow tenants.status = 'payment_overdue'.
--
-- migrations/001 created tenants.status with CHECK (status IN ('active',
-- 'suspended', 'cancelled')). migrations/065 (plan renewal) then added the
-- hourly worker job sweep_overdue_plans, which pauses a paid account whose
-- renewal has lapsed by setting status = 'payment_overdue' (login is blocked
-- in app/api/auth.py; the superadmin and billing pages count these rows). 065
-- added the supporting columns and indexes but never widened this CHECK, so
-- the moment a paid plan actually went overdue the UPDATE failed with
--   new row for relation "tenants" violates check constraint
--   "tenants_status_check"
-- and the worker retried and failed every hour (seen in the worker log on
-- 30 Oct 2026). Nothing has been affected yet only because no paid plan had
-- lapsed.
--
-- Same shape as every other CHECK in this schema (CHECK, not ENUM): drop and
-- re-add with the extra value. Re-runnable: DROP ... IF EXISTS first.
-- =============================================================================

ALTER TABLE tenants DROP CONSTRAINT IF EXISTS tenants_status_check;

ALTER TABLE tenants
    ADD CONSTRAINT tenants_status_check
    CHECK (status IN ('active', 'suspended', 'cancelled', 'payment_overdue'));
