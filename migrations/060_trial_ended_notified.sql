-- =============================================================================
-- Marks when the trial-ended email actually went out for a tenant, so
-- app/worker.py's notify_ended_trials sweep (runs hourly, same cadence as
-- the existing trial expiry sweep) sends it exactly once per tenant instead
-- of re-sending every time it runs against the same still-expired,
-- not-yet-deleted trial. Distinct from trial_expires_at itself: that's set
-- once at signup and never touched again (CLAUDE.md rule 8's immutability
-- instinct applied to trial state, not just order history).
-- =============================================================================

ALTER TABLE tenants ADD COLUMN IF NOT EXISTS trial_ended_notified_at timestamptz;
