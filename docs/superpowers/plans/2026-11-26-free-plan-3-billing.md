# Free plan, project 3: multi-month billing and lapse to Free

**Spec:** docs/superpowers/specs/2026-11-24-free-plan-design.md. Branch `project-3` in the root worktree and in the dashboard. Nothing is pushed without the owner's yes; the owner runs migration 088.

## Decisions

- Totals per period live in `app/plans.py` (integer cents) and are the only source: claims, invoices, emails, `/auth/me`. Months are 1, 3, 6 or 12.
- A renewal adds the months paid for, anchored to the old date. Same plan: unchanged rule.
- **Changing between paid plans converts unused time by value**: remaining time x old monthly price / new monthly price, then the new months from now. Otherwise a year of Starter plus one month of Business buys a year of Business.
- The claim stores months and amount at submission. The SMS must equal that amount exactly, as today.
- **Lapse to Free only when FREE_PLAN_ENABLED is on.** Off (now): pause and delete exactly as today. On: 7 days after the due date the tenant becomes Free, nothing is paused, and the paid-account deletion and its warning do nothing.
- On lapse the 10 best-selling active products of the tenant stay visible, the rest get `products.plan_hidden`. A hidden product is not on the store, in checkout, in the sitemap or in the assistant; the merchant still sees it in the dashboard. Paying again shows everything.
- A custom domain works 30 days after the lapse, then the store returns to its subdomain; the domain is remembered and reattached on upgrade.
- Admin: confirm renewal takes optional months (default 1); screens stay as they are except showing a claim's months (project 4 redoes admin).

## Tasks

1. Migration 088 + models.
2. Prices, period math, claims, invoices, emails, `/auth/me` prices, MRR. Tests.
3. `app/plan_lapse.py`: lapse, hide, restore, domain return; worker; public and assistant queries. Tests.
4. Dashboard: period choice on Billing and in the upgrade popup, totals in the payment screen, renewal banner, hidden product label, claim months in admin.
5. One review, focused tests, build.

## Safety

The test database is production: never call a sweep, notify or loop. Statements are functions tested on the test's own tenant, dangerous states only in rolled back transactions. No `free_plan_enabled` in tests except through monkeypatch on functions under test.
