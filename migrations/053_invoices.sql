-- =============================================================================
--  053_invoices.sql — event-triggered invoices, not a recurring billing engine
-- =============================================================================
--  There is no subscription-cycle system in this codebase (no Stripe, plan
--  changes are manual). An invoice is issued at two real moments: trial
--  start (a 0-cent invoice) and whenever the team manually sets/changes a
--  tenant's paid plan from superadmin — see app/api/trial.py and
--  app/api/superadmin.py. plan/amount_cents/tenant_business_snapshot are
--  captured at issue time and never touched again, same immutability
--  principle as order_items' *_snapshot columns (CLAUDE.md rule 8) — a
--  later plan change or business-details edit must never rewrite a past
--  invoice.
--
--  invoice_counters mirrors order_counters (migration 022) exactly, keyed
--  on tenant_id instead of site_id — invoices are billed per-tenant (one
--  subscription), not per-site.
-- =============================================================================

CREATE TABLE IF NOT EXISTS invoice_counters (
    tenant_id   uuid    PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
    next_number integer NOT NULL DEFAULT 1000
);

CREATE TABLE IF NOT EXISTS invoices (
    id                        uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id                 uuid        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    invoice_number            text        NOT NULL,
    plan                      text        NOT NULL,
    amount_cents              integer     NOT NULL,
    currency                  text        NOT NULL DEFAULT 'BDT',
    period_label              text        NOT NULL,
    tenant_business_snapshot  jsonb       NOT NULL DEFAULT '{}'::jsonb,
    pdf_url                   text,
    issued_at                 timestamptz NOT NULL DEFAULT now()
);

-- Every list-invoices call (Billing page) filters on tenant_id — see
-- CLAUDE.md rule 6, every new foreign key needs an explicit index.
CREATE INDEX IF NOT EXISTS idx_invoices_tenant_id ON invoices (tenant_id, issued_at DESC);
