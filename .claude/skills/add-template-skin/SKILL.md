---
name: add-template-skin
description: Scaffold a new visual design (skin) for the Next.js storefront template — a different look implementing the same section/page contract the central dashboard's theme editor already expects. Use when the user wants a new template design, a new storefront skin, or mentions building templates from scratch.
---

# Add a template skin

A "skin" is a new visual design for the storefront. It is **not** a new data
model — it renders the exact same section and page vocabulary the dashboard's
theme editor already produces (`dashboard/components/themes/editor/editor-types.ts`).
Different skins differ only in how each section *looks*, never in what data it
receives.

## The contract every skin must implement (do not deviate)

Copy these types from `dashboard/components/themes/editor/editor-types.ts`
verbatim into the template repo's `lib/theme-types.ts`. This is the single
source of truth — if the dashboard changes it, the template must match, and
vice versa is never allowed (the dashboard's design is locked).

**Section types** (`SectionType`): `banner`, `hero`, `categories`,
`featureProducts`, `productShowcase`, `whyChooseUs`, `features`,
`testimonials`, `bannerCta`, `footer`.

**Page types** (`SitePageType`): `home`, `products`, `productDetail`,
`categories`, `cart`, `checkout`, `about`, `contact`, `faq`, `privacy`,
`terms`, `notFound`.

**The settings object** (`SiteEditorSettings`) is one flat JSON blob — brand
colors, fonts, button style, header style, nav links, the ordered `sections`
array, and one group of named fields per section (e.g. `heroTitle`,
`heroBody`, `heroCta` for the hero section). Read the full type before
building anything — every field name must match exactly, since the dashboard
writes this object directly and the template only reads it.

## Steps

### 1. Confirm which repo this skin lives in

If this is the **first** skin, scaffold the shared template repo (see step 2
onward). If a template repo already exists, add a new folder under `skins/`
and skip to step 4.

### 2. New Next.js app (App Router)

```bash
npx create-next-app@latest --typescript --tailwind --app --src-dir=false
```

Add: `next/image` (built in — no local asset imports, see step 5),
Framer Motion if the design needs motion, Radix/shadcn primitives for any
interactive component (accordions, drawers, dialogs).

### 3. Data fetching — write this once, shared by every skin

One function, `lib/get-site.ts`, called from every page's server component:

```typescript
async function getSite(host: string) {
  const res = await fetch(`${process.env.BACKEND_URL}/public/site/${host}`, {
    next: { revalidate: 300, tags: [`site:${host}`] },
  });
  if (!res.ok) notFound();
  return res.json(); // { site, nav, pages, json_ld, ... }
}
```

Use Next's `generateMetadata` per page, reading the `seo` object the backend
already resolves server-side (title, description, canonical, noindex) — do
not reimplement SEO fallback logic in the template; the backend already did
that work (see `app/api/public.py`).

Add `/api/revalidate` guarded by a shared secret header
(`x-revalidate-secret`, matching `REVALIDATE_SECRET` in the backend's `.env`)
— this is what `app/worker.py`'s `handle_revalidate_site` calls after an edit.

### 4. The skin folder

```
skins/
  <skin-name>/
    sections/
      Hero.tsx
      Categories.tsx
      FeatureProducts.tsx
      ProductShowcase.tsx
      WhyChooseUs.tsx
      Features.tsx
      Testimonials.tsx
      BannerCta.tsx
      Footer.tsx
      Banner.tsx
    pages/
      ProductsPage.tsx
      ProductDetailPage.tsx
      CartPage.tsx
      CheckoutPage.tsx
      SystemPage.tsx      # about/contact/faq/privacy/terms — shared shell
    theme.css             # skin-specific design tokens only
```

Each section component takes **only** the matching slice of
`SiteEditorSettings` as props — a `Hero` component receives
`{ heroEyebrow, heroTitle, heroBody, heroCta, heroSecondary, primaryColor,
accentColor, surfaceColor, buttonStyle }`, nothing else. This keeps a skin
swappable: the page-level code that assembles sections in order never needs
to know which skin is active.

The route that renders the home page loops over `settings.sections` in
order and renders the matching component — same pattern as
`site-preview.tsx`'s `SectionBlock` switch in the dashboard, just real
components instead of a mockup.

### 5. Images — the one hard rule

**Never `import` an image file as a build asset.** The old ecom template did
this (`import w1 from "@/assets/women/1.jpg"`) and it only works for
hardcoded demo data. Every image in a real skin comes from a URL stored in
`SiteEditorSettings` or product/category data, loaded through `next/image`
with a remote loader:

```typescript
// next.config.ts
images: { remotePatterns: [{ protocol: "https", hostname: "**.supabase.co" }] }
```

If a skin needs placeholder art before the merchant uploads anything, ship a
static fallback image *in the skin's own `/public` folder*, referenced by a
default value in code — never as the only path.

### 6. Design brief — fill this in per skin, this is what actually changes

Before writing components, answer these for the new skin. Everything above
this line is fixed contract; everything below is where skins actually differ:

- **Name and vibe** (e.g. "Minimal — quiet, lots of whitespace, thin type" vs
  "Bold — saturated color blocks, heavy display type")
- **Typography pairing** (heading font / body font)
- **Default spacing scale** (tight/comfortable/generous)
- **Button shape default** (maps to `buttonStyle`: Pill / Rounded / Square)
- **Header default** (maps to `headerStyle`: Solid / Light / Minimal)
- **Motion level** (none / subtle fade-ins / full Framer Motion choreography
  like the old ecom template)
- **Card/grid treatment** for `featureProducts` and `categories` (bordered
  cards vs borderless with hover-only chrome vs image-forward with overlay
  text)

### 7. Register the skin

Add it wherever skins are selected for a deployment (env var, or a `skin`
column on `sites` / `templates` in the backend if the platform should offer a
per-customer skin picker — that's a schema decision to make explicitly with
the user, not assume).

### 8. Audit before calling it done

Before treating a skin as finished, run `/impeccable audit` against it. That
skill checks accessibility, performance, theming, responsive behavior, and
implementation integrity (repeated shortcuts, design-system drift) — things
that are easy to miss when focused on getting the section contract right.

- **First time in this template repo**: run `/impeccable init` once, before
  the first audit, so it has real product context (who the storefront is for,
  what it sells) instead of generic checks. It writes `PRODUCT.md`; answer its
  questions about the *product*, not the visual design — the design brief from
  step 6 above stays in this skill, not in Impeccable's files.
- Address P0/P1 findings before handing the skin to the user. P2/P3 are
  judgment calls — flag them, don't block on them.
- Re-run the audit after fixes; don't assume a fix worked.

## Do NOT

- Invent a new section type or page type without also updating
  `editor-types.ts` in the dashboard first — the dashboard is the source of
  truth, the template only implements what it already offers.
- Rename a field from `SiteEditorSettings` inside a skin's props — copy the
  exact field names, even if a shorter name reads better in the component.
- Build a second data-fetching or SEO layer per skin. That logic is shared
  and lives once, outside `skins/`.
- Use static image imports for anything a merchant might change.

## Finally

Tell the user which parts of the design brief (step 6) are still undecided —
a skin isn't ready to hand off to implementation until those are answered.
