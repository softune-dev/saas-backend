-- =============================================================================
--  049_tenant_trial_plan.sql — allow plan='trial' on tenants
-- =============================================================================
--  migrations/025_tenant_demo_plan_default.sql's CHECK only allowed
--  ('demo','starter','growth','business') — 'trial' (self-serve signup,
--  see app/api/trial.py) needs to be added to it, same pattern that
--  migration used.
-- =============================================================================

ALTER TABLE tenants DROP CONSTRAINT IF EXISTS tenants_plan_check;

ALTER TABLE tenants ADD CONSTRAINT tenants_plan_check
    CHECK (plan IN ('trial', 'demo', 'starter', 'growth', 'business'));
