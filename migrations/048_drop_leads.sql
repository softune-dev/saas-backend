-- =============================================================================
--  048_drop_leads.sql — remove the lead-capture funnel
-- =============================================================================
--  Superseded by self-serve trial signup (migrations/047_trial_tenants.sql,
--  app/api/trial.py) — signup now creates a real Tenant+User immediately
--  instead of a Lead row that a superadmin had to manually convert later.
--  No other table has an FK to leads (confirmed), so this is a clean drop.
-- =============================================================================

DROP TABLE IF EXISTS leads;
