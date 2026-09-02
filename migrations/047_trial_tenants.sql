-- =============================================================================
--  047_trial_tenants.sql — self-serve 3-day trial signup
-- =============================================================================
--  Trial state lives entirely on these two columns plus tenants.plan =
--  'trial' — no separate status value. Login is blocked once
--  trial_expires_at passes (see app/api/auth.py); a background sweep
--  (app/worker.py) hard-deletes tenants 4 days past that (7 days total from
--  signup). "Recovering" a trial before day 7 is just changing plan away
--  from 'trial' — the sweep then ignores it, no extra bookkeeping needed.
-- =============================================================================

ALTER TABLE tenants
    ADD COLUMN IF NOT EXISTS trial_started_at  timestamptz,
    ADD COLUMN IF NOT EXISTS trial_expires_at  timestamptz;

-- Both the login check and the sweep filter on (plan, trial_expires_at).
CREATE INDEX IF NOT EXISTS idx_tenants_trial_expiry
    ON tenants (plan, trial_expires_at)
    WHERE plan = 'trial';
