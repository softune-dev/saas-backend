# Launching a new storefront theme

A checklist for going from "new template repo exists" to "merchants can pick it
in onboarding and publish a real, working store on it." Every item here is a
real gap that shipped silently when Bazaar was added — Aurora had all of this
already, Bazaar didn't, and each one caused a real production bug before being
caught. Copy this file's checklist top to bottom for every new theme.

Terminology: a "theme" is one of the repos in `templates/` (aurora, bazaar,
sweets, ...), each its own Next.js app and its own Vercel project. The
dashboard and backend are template-agnostic — they drive any theme through the
same `PublicSiteConfig` contract (`lib/theme-types.ts` in each template).

---

## 1. Database: the `templates` row

Every theme needs exactly one row in the `templates` table (`app/models.py`'s
`Template` model). Check what already exists before adding a new one — this is
data, not a migration.

Required for the *storefront to be publishable at all*:

- `key` — matches the `TemplateKey` union in
  `dashboard/components/onboarding/onboarding-types.ts` and the theme's own
  folder name. Everything (theme defaults, previewUrl, `findSiteByTemplateKey`)
  is keyed off this string. Typo here breaks onboarding silently.
- `vercel_project_id` — the real Vercel project id (`prj_...`, found in that
  project's Settings → General). **NULL until you fill it in by hand** — there
  is no UI for this, no migration sets it, nothing prompts for it. Without it,
  `app/worker.py`'s `handle_attach_domain` logs `"has no vercel_project_id set,
  skipping"` and does nothing: every site on this theme publishes fine but its
  subdomain never actually resolves to the right deployment (see §4).
- `default_theme` / `default_pages` / `block_types` — the theme's own schema.
  Copy an existing theme's shape and adjust for the new one's actual sections.

## 2. Vercel project setup

1. Create the Vercel project from the theme's GitHub repo (one repo per theme —
   don't share a repo across two `templates/` entries).
2. **Environment Variables** (Settings → Environment Variables → Production +
   Preview) — the exact list every theme's code actually reads
   (`grep -rhoE "process\.env\.[A-Z_]+"` across `app/`, `lib/`, `components/`,
   `middleware.ts` is the authoritative way to check this for a given theme):

   | Variable | Value | Notes |
   |---|---|---|
   | `NEXT_PUBLIC_API_URL` | `https://api.softunebd.com` | The real backend. **This is the one that has bitten us twice** — see §5. |
   | `REVALIDATE_SECRET` | same value as the backend's `.env` | Must match exactly or `/api/revalidate` calls from the worker 401. |

   `SITE_HOST` / `NEXT_PUBLIC_SITE_HOST` are optional local-dev fallbacks only
   (used when there's no real Host header at all) — don't set them in
   production; real traffic always carries a real Host header.

3. **Domains** — add nothing here manually except the project's own
   `*.vercel.app` default. Do **not** try to add `*.softunebd.com` (the
   wildcard) to a second project — Vercel only lets one project own a wildcard
   domain, and it already belongs to whichever theme claimed it first (today:
   aurora, `saas-theme1`). Every other theme's sites get their *exact*
   subdomain (e.g. `myshop.softunebd.com`, not the wildcard) attached
   automatically per-site by `app/vercel.py`'s `add_domain_to_project`, which
   is exactly why §1's `vercel_project_id` has to be right — Vercel lets an
   exact hostname attached to project B win over a wildcard on project A, no
   gateway/proxy needed.

## 3. Required code in the new theme repo

Diff these files against Aurora's (the reference implementation) for any new
theme — these are the four real gaps Bazaar shipped without:

- **`middleware.ts`** must exist and set the `__preview_site` cookie with
  `sameSite: "none", secure: true`. Without the explicit flags, the cookie is
  silently dropped inside the dashboard's cross-origin preview iframe (default
  `SameSite=Lax` doesn't survive a cross-site iframe request), and the editor
  preview 404s on every in-iframe navigation.
- **`lib/checkout.ts`** — real order submission
  (`POST /public/site/{host}/orders`). Copy this file verbatim from another
  theme; it's template-agnostic. Without it, checkout has nothing to call and
  either doesn't exist or fakes success locally with no real order created.
- **`lib/theme-types.ts`** needs `PublicPaymentMethod` and
  `payment_methods?: PublicPaymentMethod[]` on the site config type — the
  checkout page can't render real payment methods without it.
- **Every build-time fetch to the backend needs `signal: AbortSignal.timeout(10_000)`**
  (`lib/get-site.ts`'s `fetchSiteConfig`, `lib/public-catalog.ts`'s
  `fetchJson`). Without a ceiling of its own, one slow/misconfigured backend
  call during static generation doesn't fail fast — it hangs until Vercel's
  own 60-second page-build timeout, retries 3x, and fails the *entire*
  production build. This is quiet until it isn't: it only shows up once
  `NEXT_PUBLIC_API_URL` is wrong or the backend is briefly unreachable during
  a build.

## 4. Dashboard wiring

- `dashboard/components/themes/themes-data.ts` — add a `previewUrl` entry
  pointing at the theme's real Vercel deployment
  (`https://saas-themeN.vercel.app`), used by the theme editor's live preview
  iframe.
- `dashboard/components/onboarding/onboarding-types.ts`'s `TemplateKey` union
  — add the new key.
- `.claude/launch.json` — add a local dev server entry on its own port, for
  local iteration against the real backend.

## 5. The mistake to not repeat: `SITE_BASE_DOMAIN`

Separate from the Vercel project's own env vars: the **backend's** `.env` has
`SITE_BASE_DOMAIN`, used to build every subdomain the worker touches
(revalidate calls, domain-attach calls, the dashboard's "Storefront address").
It defaults to `vercel.app` (`app/config.py`) — a working-sounding placeholder,
not a real gap you'd notice from a type error. If it's ever `vercel.app`
instead of `softunebd.com`, every domain-attach and revalidate call silently
targets the wrong host (`{subdomain}.vercel.app` instead of
`{subdomain}.softunebd.com`) and just quietly does nothing useful. Check this
value first if a freshly-published site's real domain never gets attached.

## 6. End-to-end verification checklist

Before calling a new theme launch-ready, actually do all of these — a clean
`tsc --noEmit` does not prove any of them:

1. `npx next build` locally succeeds. (Necessary, not sufficient — see #4:
   Vercel's build environment has failed to build things that pass this.)
2. Onboard a real test account through the dashboard onto this theme.
   Publish. Confirm the Vercel deployment for this theme actually goes
   **Ready**, not **Error** (check the project's Deployments tab — a failed
   build silently leaves production on whatever the last working build was,
   with no error surfaced anywhere in the dashboard).
3. Visit the real `{subdomain}.softunebd.com` in a real browser (not the
   dashboard preview) and confirm it's actually this theme, not another one
   served by the wildcard's default project.
4. Open the theme editor for this site and confirm the preview iframe loads
   (tests §3's middleware cookie fix).
5. Add a real product, go through checkout end-to-end with each payment
   method the site has enabled, and confirm a real order lands in
   Orders — not a fake "order placed" screen with no backend call.
