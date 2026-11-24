# Free plan, project 1: rules and server enforcement

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One plan table drives every limit and lock on the server, including AI, ChatGPT and background jobs, with no Free account existing yet.

**Architecture:** `app/plans.py` is a leaf module holding the table. `app/plan_gate.py` adapts it to routes. Locks apply to tenants whose plan is `free`; nobody can become `free` until project 5, so merging this changes only the paid plans' raised limits. `FREE_PLAN_ENABLED` (default False, read at call time) only guards the new store limit on existing plans.

**Tech Stack:** FastAPI, SQLAlchemy 2 async, Postgres (Supabase), Redis, RabbitMQ, pytest.

**Spec:** docs/superpowers/specs/2026-11-24-free-plan-design.md

## Global constraints

- Follow CLAUDE.md. Files under 500 lines. Money is integer cents. Cache and queue failures never fail a request.
- Work on branch `free-plan`. Nothing is pushed, deployed or migrated by an implementer. The owner runs migration 087 after Task 2.
- Commits carry no Co-Authored-By or "Generated with" lines.
- `plan_locked` is always HTTP 403 with detail `{"code":"plan_locked","feature":str,"needed_plan":str,"message":str}`. Product and event limit messages stay plain strings (tests/test_events.py:105 reads them).
- Existing names stay importable: `plan_cap`, `plan_product_limit`, `plan_event_limit`, `plan_storage_limit`, `PLAN_EVENT_LIMIT`, `PLAN_PRICES_CENTS`, `PLAN_NAMES`.
- Tests never depend on Redis (it is not running on the dev machine). `test_pay_device_*` fail locally for that reason; they are the known baseline.
- Reading a tenant's own plan is live from the database, never from the token or a cache.

## Out of scope (later projects)

Lapse to Free, `plan_hidden` products and multi-month billing (3). Dashboard locks, theme checkout and badge (2). Admin (4). Sign-up, phone uniqueness, trial removal, inactivity deletion (5).

## Review focus

1. Two shoppers order at the same second at 49/50: exactly one succeeds. (Task 6)
2. A dashboard tab left open for a month does not count as activity. (Task 5)
3. A `free` tenant left with `status="payment_overdue"` is never deleted by the paid-account job. (Task 5)
4. Uploading videos, or more than 100 images, cannot pass 100 MB on Free; paying merchants' counting is unchanged. (Task 7)
5. Work queued or connected while paid does not run after the plan becomes `free`: pending domain, pixel, server tracking, gateway. (Tasks 8, 9)

## Waves (parallel where files do not overlap)

- Wave 1: Task 1. Wave 2: Task 2, then the owner runs the migration.
- Wave 3, in parallel, each in its own worktree: Tasks 3, 4, 5, 6, 7.
- Wave 4, in parallel: Tasks 8, 9. Wave 5: Task 10. Then one whole-branch review.

`app/api/public.py` belongs to Task 6 in wave 3 and Task 9 in wave 4. `app/worker.py` belongs to Task 5, then Task 9. `app/api/sites.py` belongs to Task 4, then Task 8. No other task edits them in that wave.

---

### Task 1: the plan table

**Files:** rewrite `app/plans.py`; modify `app/products.py`, `app/events.py`, `app/media.py`, `app/ai.py`, `app/ai_tools.py`, `app/invoices.py`, `app/config.py`; test `tests/test_plans.py`.

**Produces:**

```python
@dataclass(frozen=True)
class Plan:
    key: str; name: str; price_cents: int
    products: int | None      # None = unlimited
    storage_bytes: int
    orders_per_month: int | None
    stores: int
    ai_per_day: int
    events: int
    features: frozenset[str]

PLANS: dict[str, Plan]        # free, starter, growth, business, trial, demo
PAID_PLANS: tuple[str, ...]   # ("starter", "growth", "business")
FEATURES: frozenset[str]
def get_plan(key: str | None) -> Plan             # unknown -> free
def has_feature(key: str, feature: str) -> bool   # unknown feature -> ValueError
def needed_plan(feature: str) -> str              # cheapest paid plan that has it
def ensure_feature(key: str, feature: str) -> None
```

Features: `customers pos dropship seo fraud domain ai chatgpt theme_switch gateways ecourier all_couriers full_analytics no_badge`. Plans are cumulative:

| | features |
|---|---|
| free | none |
| starter | customers, pos, seo, fraud, domain, ai, chatgpt, theme_switch, ecourier, full_analytics, no_badge |
| growth, business | starter's, plus gateways, all_couriers, dropship |
| trial | starter's minus theme_switch (today's behaviour) |
| demo | all |

| | products | storage | orders | stores | AI/day | events | price |
|---|---|---|---|---|---|---|---|
| free | 10 | 100 MB | 50 | 1 | 0 | 3 | 0 |
| starter | 50 | 500 MB | None | 1 | 30 | 3 | 49900 |
| growth | 500 | 2 GB | None | 1 | 100 | 10 | 149900 |
| business | None | 3 GB | None | 2 | 150 | 25 | 399900 |
| trial | 50 | 500 MB | None | 1 | 15 | 3 | 0 |
| demo | 50 | 800 MB | None | 1 | 50 | 3 | 0 |

- [ ] **Write failing tests:** every cell above asserted by parametrize; `get_plan("nonsense").key == "free"`; `needed_plan("domain") == "starter"`, `needed_plan("gateways") == "growth"`; `ensure_feature("free","seo")` raises 403 with the exact detail keys; `has_feature("free","seoo")` raises ValueError; unlimited products never raises at 100000; starter passes `ensure_theme_switch_allowed`; `get_plan` is the source for `plan_cap`, `plan_product_limit`, `plan_storage_limit`, `plan_event_limit`, `PLAN_PRICES_CENTS` and `ai_tools`' billing status (assert equality for every plan key).
- [ ] **Run:** `venv\Scripts\python.exe -m pytest tests/test_plans.py -q`. Expected: FAIL.
- [ ] **Implement.** `plans.py` imports only the standard library and fastapi. `ensure_courier_allowed`, `ensure_payment_provider_allowed`, `ensure_theme_switch_allowed` keep their names and use the table: Steadfast is always allowed, `ecourier` needs `ecourier`, the rest need `all_couriers`. Delete the duplicate AI dict in `ai_tools.py`. `plan_product_limit` returns `int | None`. Add `free_plan_enabled: bool = False` to config.
- [ ] **Run** the same plus `tests/test_events.py`. Expected: PASS.
- [ ] **Commit** `One plan table: limits, features and prices in app/plans.py`.

### Task 2: migration 087 and models

**Files:** create `migrations/087_free_plan.sql`; modify `app/models.py`, `app/schemas.py` (`TenantPlan` adds `"free"`), `app/crud.py` (`_explain`, set `last_active_at` at creation). Use the `/add-migration` skill.

**Produces:** `Tenant.last_active_at`; `TenantOrderMonth(tenant_id, month: date, orders: int, blocked: int)` with PK `(tenant_id, month)`.

- [ ] Migration, idempotent: `tenants_plan_check` adds `free`; `tenants.last_active_at timestamptz NOT NULL DEFAULT now()` (every clock starts at launch); `tenant_order_months` with `ON DELETE CASCADE` and RLS as in 054 (the PK's leading column serves the FK); marketing_contacts lifecycle CHECK adds `free`; notifications type CHECK adds `order_limit`. Check whether `order_flagged` (published in public.py) is missing from that CHECK; if so add it and say so in the commit.
- [ ] Mirror in models. `python -c "import app.main"` succeeds.
- [ ] **Commit** `Migration 087: free plan, activity time, monthly order counter`. **Stop: the owner runs it.**

### Task 3: route gates for customers, POS, fraud, analytics, Meta

**Files:** create `app/plan_gate.py`, `app/api/locked_preview.py`; modify `app/api/customers.py`, `manage.py`, `fraud.py`, `marketing.py`, `analytics.py`, `commerce.py`, `app/api/__init__.py`; tests `tests/test_plan_gates.py`, `tests/test_tenant_isolation.py`; add `account_factory(plan)` to `tests/conftest.py`.

**Produces:**

```python
async def tenant_plan(db, tenant_id) -> str
async def ensure(db, tenant_id, feature: str) -> None
def require_feature(feature: str)   # FastAPI dependency using CurrentUser and get_db
```

- [ ] Failing tests with a `free` account, each 403 `plan_locked`, and the same call on `starter` not locked: list/get/patch/delete customers; list/delete/clear abandoned checkouts; every fraud route; every marketing route except disconnect; `POST /sites/{id}/orders` with channel `pos`, with `storefront` and with no channel; product list by SKU scan is still open.
- [ ] Analytics on `free`: `weeks=26` returns at most 7 days, only revenue, orders and visits populated, and `GET /sites/{id}/orders` returns at most the current and previous month. `starter` unchanged.
- [ ] A cached response from a paid period is not served after the plan becomes `free` (gate before `cache.get_json`).
- [ ] `GET /sites/{id}/locked-preview/{feature}` returns `{"count": int}` only, for `customers` and `fraud`; other tenant gets 404; unknown feature 404.
- [ ] Checkout by a shopper still creates the customer row on `free`.
- [ ] OrderOut on `free` returns `ip_address`, `device_id`, `fraud_status`, `fraud_reason` and `customer_id` as null; buyer name, phone and address stay.
- [ ] **Commit** `Plan gates: customers, POS, fraud, analytics, Meta`.

### Task 4: store limit, SEO, domain and theme through /sites and /pages

**Files:** modify `app/api/sites.py`, `app/api/pages.py`, `app/domains.py`; test `tests/test_plan_sites.py`. **Consumes:** `plan_gate.ensure` (stub locally until Task 3 merges, same signature).

- [ ] Failing tests on `free`: PATCH with `seo`, `fraud_rules` or a non-null `custom_domain` is 403; `custom_domain: null`, `theme`, `business`, `about`, `faqs`, `legal`, `shipping`, `assistant` are allowed; page create/update with `seo` is 403 and without it is allowed; switch-theme is 403 and on `starter` succeeds.
- [ ] Gate as a route dependency, not in the handler body, so `ai_setup.apply_site`'s direct call still writes its SEO description: test that calling `sites.update_site(...)` directly on a `free` tenant still saves `seo`.
- [ ] `GET domain-status` and `domains.promote` never promote a pending domain on `free`.
- [ ] POST /sites: `free` beyond 1 is always 403; other plans are limited only when `free_plan_enabled`. Duplicate subdomain still returns 409 first. DELETE on `free`'s only store is 403, because delete then create would switch the theme.
- [ ] **Commit** `Plan gates: stores, SEO, domain, theme`.

### Task 5: activity tracking and deletion guards

**Files:** create `app/activity.py`; modify `app/security.py` (`get_principal`), `app/oauth_server.py` (`authenticate_bearer`), `app/api/trial.py` and the refresh route in `app/api/auth.py` (set `last_login_at`), `app/worker.py`, `app/billing_actions.py`; test `tests/test_activity.py`.

**Produces:** `async def touch(tenant_id, *, path: str = "") -> None`: best effort, awaited inline, own short session, at most hourly. The throttle is an in-process dict, so Redis is not needed.

- [ ] Failing tests: an authenticated request moves an old `last_active_at` to now; a second within the hour does no write; `/notifications` and `/ai/usage` polling do not move it; a database error in `touch` still returns 200; sign-up completion and token refresh set `last_login_at`.
- [ ] `notify_plan_deletion_warning` and `sweep_abandoned_paid_accounts` act only on `plans.PAID_PLANS`: a `free` tenant with `payment_overdue` and an old `plan_overdue_since` survives a sweep.
- [ ] `apply_paid_plan` with a stale past `plan_renews_at` on a tenant that was not on a paid plan anchors to now.
- [ ] **Commit** `Track real activity; paid-account deletion never touches free`.

### Task 6: 50 orders a month

**Files:** create `app/order_limit.py`; modify `app/api/public.py`; test `tests/test_order_limit.py`.

**Produces:** `current_month() -> date` (first day, Asia/Dhaka); `async def claim(db, tenant) -> bool`, one statement: `INSERT ... ON CONFLICT DO UPDATE SET orders = orders + 1 WHERE orders < :limit RETURNING orders` (no WHERE when unlimited); `async def record_blocked(tenant_id)` in its own session; `async def is_open(db, tenant) -> bool`.

- [ ] Failing tests: order 50 succeeds, order 51 is 403 `{"code":"order_limit","whatsapp":"8801..."}` and creates no order, customer or stock change; `blocked` becomes 1; deleting all orders does not reset it; deleting the site does not reset it; a failed order (no stock) does not count; `starter` counts but is never blocked; the month turns over at Dhaka midnight (patch the clock); **two concurrent orders at 49: exactly one succeeds**.
- [ ] Call `claim` immediately after `next_order_number`, before `crud.save`, in the same transaction.
- [ ] Site config returns `ordering: "checkout" | "whatsapp"` and `badge: bool`; crossing 50 calls `cache.invalidate_site` and queues revalidation. At 40 and 50 one `order_limit` notification each, never twice.
- [ ] **Commit** `Free plan: 50 storefront orders a month, counted where deleting cannot reset it`.

### Task 7: products, events and storage

**Files:** modify `app/media.py`, `app/api/media.py`, `app/api/commerce.py` (create only), `app/api/events.py` (create only), `app/ai_actions.py`, `app/ai_agent_products.py`, `app/mcp_images.py`, `app/ai_site_setup.py`, `app/api/ai_images.py` (storage check only); test `tests/test_plan_limits.py`. `commerce.py` is also Task 3's: this task merges after it.

- [ ] Failing tests: product 11 and event 4 on `free` are 403 through HTTP, the AI confirm route, the agent tool and MCP; unlimited on `business`; two concurrent creates at 9 products leave exactly 10 (lock the tenant row `FOR UPDATE` before counting).
- [ ] `site_storage_used_bytes(subdomain, *, full: bool = False)`: `full` counts videos and paginates past 100; used for `free` only. Cloudinary is faked in tests. With `full=False` the result is identical to today.
- [ ] Every upload path (media image, media video, save-to-gallery, MCP image, AI setup image) refuses past 100 MB on `free`. Cloudinary calls go through `run_in_threadpool`.
- [ ] **Commit** `Free plan: product, event and storage limits on every path`.

### Task 8: AI, agent, ChatGPT

**Files:** modify `app/api/ai.py`, `app/api/ai_agent.py`, `app/api/ai_images.py`, `app/ai_agent.py`, `app/ai_agent_settings.py`, `app/ai_agent_admin.py`, `app/ai_agent_undo.py`, `app/ai_tools.py`, `app/mcp_server.py`, `app/api/oauth.py`, `app/api/sites.py` (undo paths only); test `tests/test_plan_ai.py`.

- [ ] Failing tests on `free`, 403 `plan_locked`: suggest, chat, agent chat, generate-text, all seven `/ai` confirm actions, undo, image generate and edit even with purchased credits, both credit purchases. A purchase already submitted before the change still settles.
- [ ] Still works on `free`: AI setup plan, image, apply; `/ai/images/generate` with `use_onboarding_free=true` while free images remain and onboarding is incomplete, then locked; presets, balance, save-to-gallery; product text in the setup wizard (`/ai/chat` is used by step-products; allow it only while `onboarding_completed_at` is null).
- [ ] `art_direct` never runs before the credit or free-image check (today a zero-credit account gets unlimited billable text calls; fix for every plan).
- [ ] Feature map for agent tools, used by the agent, by MCP and by undo: customers, SEO, fraud, domain tools. A write or undo of a locked column is refused after a downgrade.
- [ ] MCP on `free`: `tools/call` returns a plain-sentence error with no plan or price text (the existing no-upgrade rule), the token is not revoked, and it works again after upgrading. Consent is refused with a clear message. Listing and disconnecting connections stay open. Dict details are converted to their `message`, never shown as a Python dict.
- [ ] **Commit** `Plan gates: AI, agent, undo and ChatGPT`.

### Task 9: things that keep running

**Files:** modify `app/worker.py`, `app/api/public.py`, `app/assistant/service.py`, `app/domains.py`, `app/billing_actions.py`, `app/api/superadmin.py`; test `tests/test_plan_runtime.py`.

- [ ] Failing tests on `free`: site config omits pixel, GTM, GA and TikTok ids and gateway payment methods; `init_gateway_payment` refuses; checkout refuses a `payment_method` that is not COD, manual or Softune Pay; CAPI, attach-domain and pending-domain jobs do nothing; basic blocklist enforcement still runs.
- [ ] A paid merchant's config is byte-identical to before (snapshot test).
- [ ] Any plan change (claim, superadmin) invalidates the site cache and queues revalidation.
- [ ] Superadmin: setting `free` creates no invoice and no renewal date; confirm-renewal on `free` is 400.
- [ ] **Commit** `Plan gates: background jobs, public config and payments`.

### Task 10: what the dashboard reads, and shared lists

**Files:** modify `app/api/auth.py`, `app/schemas.py`, `app/contacts.py`, `app/api/superadmin_billing.py`, `superadmin_overview.py`, `superadmin_ops.py`, `superadmin_marketing.py`; test `tests/test_plan_me.py`.

- [ ] `/auth/me` adds `plan_info: {key, name, features: list[str], limits: {...}, usage: {products, orders_this_month, blocked_this_month, events, stores}}`. Exact for each plan. Storage is not included (it is a slow Cloudinary call; the media page already returns it).
- [ ] Contacts lifecycle for `free` is `free`, or `former_customer` when `ever_paid`.
- [ ] The four copies of the paid-plan list read `plans.PAID_PLANS`. Superadmin totals and MRR are unchanged for existing data, with `demo` excluded from customer counts.
- [ ] **Commit** `Plan details on /auth/me; one paid-plan list`.

## Done means

Full suite green except the known Redis baseline, isolation file untouched except additions, one whole-branch review with no open findings, and a before/after comparison of the 3 paying merchants' public config showing no difference. Deploying is a separate yes from the owner.
