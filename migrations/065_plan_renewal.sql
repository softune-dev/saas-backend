-- =============================================================================
-- Recurring plan renewal tracking for paid tenants. There's still no payment
-- gateway (see dashboard/components/billing/billing-data.ts's own docstring)
-- so this doesn't bill anyone automatically — it's what lets app/worker.py
-- remind a paying merchant their subscription is due, warn them when it's
-- overdue, pause dashboard access if nothing is heard back, and only as a
-- LAST resort — a full extra month after that, never the trial's 7 days —
-- delete an abandoned paid account. A paying customer likely has real
-- accumulated business data (products, orders, months of history), so the
-- deletion window here is deliberately far more forgiving than a trial that
-- never converted.
--
-- plan_renews_at anchors to the ORIGINAL purchase/renewal date, not "today +
-- 30 days" recomputed from whenever a late payment actually lands — see
-- app/api/superadmin.py's confirm_plan_renewal, which always advances it by
-- exactly one calendar month from its own previous value, so a merchant who
-- pays a few days late still renews on the same day next month, not a
-- drifting one.
--
-- The four _at guard/marker columns exist for the exact reason
-- trial_ended_notified_at does (migrations/060): so the hourly sweep sends
-- each reminder exactly once per stage instead of re-sending on every tick
-- while the condition remains true. All four get cleared back to NULL every
-- time plan_renews_at advances (a fresh cycle needs a fresh timeline).
-- =============================================================================

ALTER TABLE tenants ADD COLUMN IF NOT EXISTS plan_renews_at timestamptz;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS plan_renewal_reminded_at timestamptz;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS plan_overdue_notified_at timestamptz;
-- When app/worker.py's sweep_overdue_plans actually set status =
-- "payment_overdue" (login paused) — the anchor the 1-month deletion
-- countdown below counts from, not plan_renews_at itself (that would
-- silently shrink the deletion window by however many grace days already
-- elapsed getting here).
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS plan_overdue_since timestamptz;
-- Guards the one "your account will be permanently deleted soon" email
-- sent partway through the post-suspension month, same one-shot-per-stage
-- reasoning as the other three.
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS plan_deletion_warned_at timestamptz;

-- INDEX: notify_upcoming_renewals / notify_plan_overdue both filter on
-- plan_renews_at for every paid tenant — partial on NOT NULL since
-- trial/demo tenants never set it and would otherwise bloat the index for
-- nothing.
CREATE INDEX IF NOT EXISTS idx_tenants_plan_renews_at
    ON tenants (plan_renews_at)
    WHERE plan_renews_at IS NOT NULL;

-- INDEX: sweep_overdue_plans (pause) and the abandoned-account deletion
-- sweep both filter on plan_overdue_since — partial on NOT NULL for the
-- same reason as above (only ever set while status = "payment_overdue").
CREATE INDEX IF NOT EXISTS idx_tenants_plan_overdue_since
    ON tenants (plan_overdue_since)
    WHERE plan_overdue_since IS NOT NULL;
