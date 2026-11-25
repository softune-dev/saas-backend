-- =============================================================================
-- 089_free_launch.sql: the Free plan replaces the 7-day trial.
--
-- RUN AFTER the new backend is deployed, not before: while the old code is live
-- it still creates trial accounts, which would need converting again.
--
--   A. Every remaining trial account becomes Free. The new code has no trial
--      expiry, so a leftover 'trial' row would keep paid features for good.
--      (On 26 Nov 2026 there were none.)
--   B. An index for the one-Free-store-per-phone check at sign-up.
--
-- Nothing is deleted. Safe to run twice. The 'trial' value stays allowed in
-- tenants_plan_check: removing it would break old rows in other tools for no gain.
-- =============================================================================

-- A. ----------------------------------------------------------------------------
BEGIN;
SET LOCAL lock_timeout = '5s';

UPDATE tenants
   SET plan = 'free', last_active_at = now()
 WHERE plan = 'trial';
COMMIT;

-- B. ----------------------------------------------------------------------------
BEGIN;
SET LOCAL lock_timeout = '5s';

-- Serves app/free_signup.py: "does a Free store's owner already use this
-- number?", run on every sign-up. The expression must match that file's exactly.
CREATE INDEX IF NOT EXISTS ix_users_phone_last10
    ON users ((right(regexp_replace(coalesce(phone, ''), '\D', '', 'g'), 10)));
COMMIT;

-- Check afterwards (expect 0, then 1 row):
--   SELECT count(*) FROM tenants WHERE plan = 'trial';
--   SELECT indexname FROM pg_indexes WHERE indexname = 'ix_users_phone_last10';
