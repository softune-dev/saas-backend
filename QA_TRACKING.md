# QA Tracking — Security, Bugs, Red Flags

Generated from a direct audit of the running codebase (backend, dashboard, and
all three storefront templates), not a generic checklist. Every item below is
either a real finding verified by reading the actual code (and in several
cases testing it live against the real database), or an already-known gap
from work done earlier that's still unresolved. Nothing here is guessed.

**Columns:** Status = `Pending` / `In Progress` / `Done` / `Won't fix`.
Priority = `P0` (exploitable / data-affecting, fix now) → `P3` (nice-to-have
/ cosmetic).

Update this file directly as items get worked — flip Status, add a one-line
note under "Notes" with the commit hash once fixed.

---

## Security

| # | Priority | Status | Area | Finding | Notes |
|---|---|---|---|---|---|
| S1 | **P0** | **Done** | Auth | `POST /auth/login` had **no rate limiting** — unlimited password-guessing attempts against any known email. Added `login_rate_limit`: a per-IP window (20/5min, generous — shared NAT/office) and a per-email window (8/5min, tight) enforced independently. | `app/ratelimit.py`, `app/api/auth.py`. |
| S2 | **P1** | **Done** | Auth | No `/auth/logout` and no server-side revocation for refresh tokens (14-day validity) — a stolen refresh token stayed valid for up to 14 days with no way to kill it. Added a real `POST /auth/logout` that revokes both the current access token and the submitted refresh token via a Redis deny-list keyed by each token's `jti`. | `app/security.py` (`revoke_token`), `app/api/auth.py`. Verified logic via direct test (Redis wasn't reachable locally at test time — fail-open behavior confirmed correct; full revocation behavior needs a re-run once Redis is up). |
| S3 | **P1** | **Done** | Auth | Deactivating a user (or changing their password) didn't take effect until their access token naturally expired (up to 30 min), since `get_principal` never hits the DB. Added `revoke_all_user_tokens(user_id)` — a Redis timestamp compared against each token's `iat`, so every token issued before that moment is rejected regardless of its own expiry. Wired into `/auth/change-password`, which now kills every other session on a password change. | `app/security.py`, `app/api/auth.py`. Same re-verify note as S2. |
| S4 | **P2** | **Won't fix (accepted tradeoff)** | Payments | Manual payment (bKash/Nagad) checkout accepts a merchant-entered transaction ID with no verification against the real payment. User decision: leave as trust-based — small stores already verify manually before shipping in practice. Documented here as a conscious, accepted risk, not a bug. | Decided 2026-08-25. Revisit if fraud actually happens. |
| S5 | **P3** | **Done** | Landing page | `landing/app/support/contact/page.tsx` had a fabricated "Priority Call Desk" (fake US phone number) and "Live Chat Widget" (dead link) — removed both, kept only the real Email Support channel. Also found and fixed a second issue while in there: the contact **form itself was fake** — `handleSubmit` just flipped a "Message Sent!" UI state with no message ever actually sent anywhere. Now builds a real `mailto:` link with the message pre-filled and opens it, with honest copy about what actually happens. | `landing/app/support/contact/page.tsx`. |

## Data integrity / tenant isolation

| # | Priority | Status | Area | Finding | Notes |
|---|---|---|---|---|---|
| D1 | **P0** | **Done** | Backend | Public self-signup (`POST /auth/register`) let anyone create a free account on a paid-only public domain — closed this session, replaced with `scripts/create_account.py`. | Commit `97669cd`. |
| D2 | **P0** | **Done** | Onboarding | `templateKey` defaulted to `"aurora"` for every tenant regardless of their real site's template — broke publish, catalog pickers, and screenshot capture for any non-Aurora site. Fixed to resolve from the real `site.template_id`. | Commit `e72ac41`. |
| D3 | **P1** | **Done** | Customers | `get_or_create_customer` looked for a `"name"` key that never exists in the real checkout payload (which sends `first_name`/`last_name`), so `Customer.name` was always `None` — every real customer showed "Unnamed." Two already-affected real customer rows backfilled directly. | Commit `a762643`. |
| D4 | **P2** | Pending | Templates | `templates/sweets` has **no `middleware.ts` at all** — the dashboard's theme-editor preview iframe mechanism (the `?__site=`/cookie override every other template has) doesn't exist there. Onboarding a real merchant onto Sweets today would mean a broken editor preview, on top of whatever other feature gaps it has (untested — Bazaar alone had 4 missing sections, a broken checkout, wrong SEO metadata, and a stale font/domain config before this session's fixes; Sweets has never been audited the same way). | Recommend running the same pass this session did on Bazaar before selling anyone a Sweets site. |
| D5 | **P2** | Pending | Backend | No real billing/payment collection anywhere in the backend — `Tenant.plan` is set by hand (via `scripts/create_account.py`) after payment is received out-of-band. This is a conscious, documented tradeoff for the current soft-launch stage, not a bug, but it means there's no automated enforcement if a plan should downgrade/expire. | Track as a roadmap item, not an urgent fix. |

## Performance / scale

| # | Priority | Status | Area | Finding | Notes |
|---|---|---|---|---|---|
| P1a | **P1** | **Done** | Database | Full FK audit (queried `pg_constraint`/`pg_index` directly) found `help_tickets.user_id` had zero index coverage, and both `help_tickets` and `customers` only had single-column indexes where their real list queries filter one column and sort by `created_at`. Added composite indexes; verified with a 200k-row benchmark — 108ms → 0.14ms (~780x) on the real customers-list query. | Migration `033_indexing_pass.sql`, commit `f71dc56`. Already applied to the live DB. |
| P2a | **P1** | **Done** | Storefronts | Product detail page (`/shop/[slug]`) fetched the product, then waited for that to finish before fetching related products/categories — a real waterfall, not parallel. Fixed in both Aurora and Bazaar; added a `loading.tsx` skeleton so navigation shows instant feedback instead of a blank page. | Commits `f8cc6da` (aurora), `628ecee` (bazaar). |
| P3a | **P3** | Pending | Backend | Screenshot capture job used to sleep 8 minutes before capturing (now 90s) and preferred `custom_domain` over subdomain (now subdomain-first) — fixed this session. Worth a follow-up: confirm no other worker job still has an oversized fixed sleep left over from before `SITE_BASE_DOMAIN` was corrected. | Spot-checked `handle_capture_screenshot` only; `handle_revalidate_site` and `handle_attach_domain` weren't re-audited for the same class of stale-assumption bug. |

## Correctness / real bugs (fixed this session, listed for the record)

| # | Priority | Status | Area | Finding | Notes |
|---|---|---|---|---|---|
| B1 | P0 | **Done** | SEO | Root layout in both templates never read the merchant's actual SEO settings (favicon, OG image/title, indexing) — built a generic title/description from scratch. Also caused a double-suffixed title bug ("Shop \| Niyenen \| Niyenen") as a direct side effect of the first fix. | Commits `083240c`/`2c02383` (bazaar), `bc4c935`/`9b358f9` (aurora). |
| B2 | P1 | **Done** | Bazaar | 4 homepage sections (Product Showcase, Category Showcase, Testimonials, Banner CTA) were deliberately no-op'd in `SectionRenderer.tsx` — dashboard editor let merchants configure them, storefront silently rendered nothing. Built real components for all four. | Commit `1b7053f`. |
| B3 | P1 | **Done** | Bazaar | Checkout was entirely fake — no API call, hardcoded COD, `setDone(true)` locally. Ported the real order-submission flow from Aurora. | Commit `06691b9`. |
| B4 | P1 | **Done** | Bazaar | About/Contact/FAQ/Privacy/Terms/Cart/Checkout used static hardcoded page titles or (Contact) no metadata at all, silently inheriting the wrong page's title. Contact also had a fabricated fake phone/email. | Commit `2c02383`. |
| B5 | P2 | **Done** | Dashboard | `defaultSiteSettingsByTheme` had fabricated demo content (fake testimonials, CTA copy, honey-shop footer description) baked into Aurora's and Sweets' placeholder defaults — onboarding silently persisted this to real customer sites since no onboarding step ever touches those fields. | Commit `68f3474`. |
| B6 | P2 | **Done** | Editor | Every image picker in the theme editor uploaded to Cloudinary the instant a file was picked, even if never published — wasted storage. Now defers to a local blob preview, uploads for real only at Publish. | Commit `68f3474`. |

---

## How to use this file

- Work top to bottom by priority within each section — Security P0/P1 first.
- When you fix something, flip Status to `Done` and add the commit hash to Notes.
- If you decide something is intentionally out of scope (like S4), don't
  delete the row — change Status to `Won't fix (accepted tradeoff)` with a
  one-line reason so the decision is on record, not silently lost.
- Anything genuinely new you find while working through this list belongs as
  a new row, not a comment buried in a PR — keep this the one source of truth.
