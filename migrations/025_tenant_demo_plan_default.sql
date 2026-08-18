-- No "free" plan: unassigned tenants default to "demo" instead. The real
-- plan lineup is demo/starter/growth/business (001_core.sql's original
-- check constraint still listed the old pre-launch names). Existing rows
-- are migrated before the new constraint is added, since old values like
-- 'free' would otherwise violate it immediately.
ALTER TABLE tenants DROP CONSTRAINT IF EXISTS tenants_plan_check;

UPDATE tenants SET plan = 'demo' WHERE plan NOT IN ('demo', 'starter', 'growth', 'business');

ALTER TABLE tenants ADD CONSTRAINT tenants_plan_check
    CHECK (plan IN ('demo', 'starter', 'growth', 'business'));

ALTER TABLE tenants ALTER COLUMN plan SET DEFAULT 'demo';
