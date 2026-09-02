-- =============================================================================
--  051_site_onboarding_completed.sql — track the dashboard wizard, not status
-- =============================================================================
--  sites.status alone can't gate the "Getting Started" sidebar item: a trial
--  signup (app/api/trial.py) auto-publishes the site immediately so the
--  merchant sees a live store right away, before ever touching the wizard.
--  A dedicated timestamp, set once by StepFinish's own publish action, is
--  the only honest signal that the merchant actually walked through Setup.
-- =============================================================================

ALTER TABLE sites
    ADD COLUMN IF NOT EXISTS onboarding_completed_at timestamptz;
