# Softune — Full Context for Content/Blog Writing

This file exists to be pasted into any LLM (ChatGPT, Claude, Gemini, whatever)
as a single source of truth about the product, so you don't have to re-explain
it from scratch every time you want blog ideas, ad copy, landing page copy,
or competitor comparisons. It is written to be complete, not persuasive —
every claim here is either something that's actually built and working, or
explicitly marked as "not built yet" / "roadmap." Don't let an LLM invent
features that aren't listed here as live.

---

## 1. What Softune Is, One Paragraph

Softune is a multi-tenant SaaS site builder for e-commerce, built specifically
for the Bangladesh market. A merchant signs up, picks a pre-built storefront
template, and gets a live, real online store with a full admin dashboard —
products, orders, couriers, payments, fraud protection, an AI theme
assistant, and real-time order notifications — without writing a line of
code or hiring an agency. It is NOT a website builder for generic sites
(portfolios, blogs, brochures) — every template is an e-commerce storefront
with cart, checkout, and order flow already built in.

Think "Shopify, but built around how Bangladeshi merchants actually operate":
Cash on Delivery as a first-class payment method, real courier integrations
(Steadfast, with Pathao/RedX groundwork laid), BDT as the native currency,
and manual bKash/Nagad transfer support for merchants without a payment
gateway account (most don't have one).

## 2. Who It's For

- Small-to-medium Bangladeshi merchants currently selling through Facebook/
  Instagram DMs or WhatsApp, who want a real storefront without the cost of
  hiring a web agency (agencies here typically charge ৳15,000–50,000+
  one-time for a WooCommerce-based site, plus hosting).
- Merchants who find generic no-code builders (Shopify, Wix, generic
  WooCommerce templates) either too expensive in USD terms, or missing
  BD-specific things like COD-first checkout and local courier integration.
- Agencies/resellers who want to spin up client stores fast (the template
  system + theme editor is built to support this workflow, though a formal
  agency/reseller tier is not built yet — see Section 9).

## 3. The Competitive Landscape (verify/update before quoting numbers)

This section reflects general knowledge of the Bangladesh e-commerce SaaS
space, not a freshly re-verified market scan — prices and feature sets in
this market change fast, so treat specific numbers below as "roughly this
ballpark as of when this was written," and re-check before publishing
anything with a hard number in it.

- **WooCommerce/Dokan agency builds** — the most common path today. A local
  agency builds a WordPress + WooCommerce store, one-time cost roughly
  ৳15,000–50,000+, then the merchant pays for hosting separately and often
  needs the same agency (or a developer) for any future change. No built-in
  fraud tooling, no AI, no unified courier/payment dashboard — everything is
  a separate WordPress plugin, often with plugin-conflict headaches.
- **Local "store in a box" SaaS tools** (various small BD players) — cheap
  (~৳500–3,000/month) but usually a fixed template with very limited
  customization, weak or no theme editor, and thin feature sets (often just
  product listing + COD checkout, no analytics, no fraud tools).
- **Shopify** — powerful, but priced and built for a global/USD market.
  Monthly cost in BDT terms is high for a typical BD merchant, payment
  gateway options assume Stripe-style rails that don't map cleanly onto
  bKash/Nagad/COD-first buying behavior, and courier integration for BD
  couriers isn't native.
- **Dukaan and similar mobile-first store builders** — fast to set up, but
  generally thinner on customization (theme editor depth) and on the
  merchant-operations side (fraud tooling, order/courier workflow depth)
  compared to what Softune has built.

**Where Softune is positioned**: not the cheapest option on the market, and
deliberately so — the pitch is "you get agency-quality customization and
real operational tooling (fraud, couriers, real order management, AI
assistance) at a fraction of an agency's one-time cost, without becoming a
stripped-down template tool." See Section 8 for the actual pricing.

## 4. Architecture (for any blog post that goes technical)

- **Backend**: FastAPI (async Python), SQLAlchemy 2.0 + asyncpg, Postgres
  (hosted on Supabase), Redis (caching), RabbitMQ (background job queue).
- **Multi-tenant by design**: every tenant-owned table is scoped by
  `tenant_id`, enforced at the database-query layer (not just the API
  layer) — cross-tenant access returns 404, not 403, so an attacker can't
  even confirm another tenant's data exists.
- **Storefronts are separate Next.js apps**, not server-rendered by the
  main backend. The backend exposes a public, heavily-cached
  `GET /public/site/{host}` endpoint; each storefront template calls it to
  render. This is what makes "edit in the dashboard, see it live in
  seconds, no redeploy" possible — editing content never touches the
  storefront's actual codebase or triggers a rebuild.
- **Background worker** (separate process, RabbitMQ-driven) handles
  anything that shouldn't block a user-facing request: cache
  revalidation on publish, sitemap regeneration, order notification
  delivery (dashboard bell + push), and a daily data-retention sweep.
- **Money is stored as integer cents** everywhere — no float rounding
  bugs in prices.
- **Order history is immutable**: past orders store a snapshot of product
  name/price at time of purchase, so a later price change never rewrites
  what a customer was actually charged.

## 5. Storefront Templates (what a merchant's actual site looks like)

Three live templates today, each a full Next.js e-commerce site sharing the
same backend contract (same section/page system, same admin dashboard):

- **Aurora** — the primary/most complete template. Fashion/lifestyle
  aesthetic, serif display fonts, editorial feel.
- **Bazaar** — general marketplace-style storefront.
- **Sweets** — food/bakery-oriented storefront.

Every template's homepage is built from a fixed set of section types the
merchant can add, remove, and reorder in the dashboard's theme editor:
banner, hero, categories, featured products, product showcase, category
showcase, "why choose us," features, testimonials, banner CTA, and footer.
None of these sections show fabricated/placeholder content in production —
if a merchant hasn't configured a section (e.g. hasn't picked featured
products yet), it simply doesn't render, rather than showing sample data.

Every template includes, out of the box:

- Full product catalog with categories, variants (size/color/etc. with
  optional per-variant pricing), feature highlight callouts, and image +
  video galleries (uploaded video or pasted YouTube/Vimeo link).
- Real cart and checkout — not a mockup. Checkout computes shipping and
  totals server-side from real product/delivery-charge data (never trusts
  client-submitted prices), validates the customer's phone number is a
  real Bangladeshi mobile number (all seven real operator prefixes:
  013/014/015/016/017/018/019), and creates a real order the merchant sees
  in their dashboard instantly.
- Checkout only offers payment methods the merchant has actually connected
  in their dashboard (Section 7) — never a hardcoded fake default.
- 40 curated display fonts and 40 curated body fonts, full color/branding
  controls, all changeable live in the theme editor with instant preview.

## 6. Dashboard — Full Feature Inventory

Everything below is a real, working page in the merchant's admin dashboard
(not a mockup), unless explicitly marked otherwise:

- **Dashboard home** — store overview: sales snapshot, top products,
  quick links, empty-state guidance for brand-new stores.
- **Categories** — create/edit/reorder, with banner images and icons.
- **Products** — full CRUD: pricing (with compare-at/"was" pricing),
  stock tracking, SKU/serial number, variants with optional per-variant
  pricing, rich-text description editor (image uploads inline), short
  description for SEO, video (upload or link), feature highlight callouts,
  per-product delivery charges by location, free-delivery toggle.
- **Orders** — real order list with status flow (pending → paid →
  fulfilled, plus cancelled/refunded), order detail view showing customer
  info, shipping address, payment method + manual-payment transaction ID,
  itemized products, and totals.
- **Courier** — connect real courier accounts. Steadfast has a live,
  verified connect flow (API key checked against their real API before
  saving); Pathao and RedX have the schema/UI in place but aren't live
  yet.
- **Payments** — connect payment methods for checkout: Cash on Delivery,
  Manual (customer transfers to the merchant's own bKash/Nagad number and
  submits a transaction ID, merchant verifies by hand), and groundwork for
  real gateway accounts (bKash/Nagad/SSLCommerz/Rocket — credentials can be
  stored, but there's no live checkout flow through them yet, see
  Section 9).
- **Analytics** — real numbers computed from actual order data: revenue,
  average order value, best-sellers, category share, refund rate. No
  fabricated "conversion rate" metric, since there's no visit-tracking
  system to honestly compute one from.
- **Themes** — the live theme editor: colors, fonts, button style,
  navigation, header, every homepage section's content, hero images,
  testimonials, footer. Includes an "Ask AI" assistant (Section 7) for
  theme suggestions.
- **Customers** — auto-derived customer list from order history (name,
  contact, order count) — not a separate signup/account system, since
  storefront checkout is guest-only by design (no forced account creation
  for buyers).
- **Site Settings** — business info, SEO (meta tags, sitemap, analytics/
  pixel IDs), shipping/delivery locations, FAQs, legal pages
  (privacy/terms).
- **Fraud Protection** — a merchant-maintained phone number blocklist,
  plus three checkout-time rules evaluated against the current order only
  (no order history needed, which matters because a brand-new store has
  none): hold first-time high-value orders for review, flag burst orders
  from one phone number within a time window, and block blocklisted
  numbers outright. A blocked checkout attempt is rejected server-side
  (not just a UI warning) and the merchant is notified immediately.
- **Billing** — page exists in navigation; real subscription billing
  enforcement is not built yet (see Section 9).
- **Notifications** — a bell icon with unread badge, animated on new
  activity, backed by real data (not polling a mock). Fires for new
  orders, blocked fraud attempts, and site publish/unpublish. Includes a
  synthesized notification sound (Web Audio API, no audio file needed) and
  optional real OS-level push notifications (Web Push, works even with the
  dashboard tab/browser closed) once a merchant opts in.
- **AI Assistant** — a chat-style sidebar (Gemini-backed) that can answer
  store questions and suggest theme changes (colors/fonts) within a
  curated, validated set of options — it cannot suggest something the
  editor doesn't actually support.

## 7. Payments, Couriers, and Fraud — the Operational Core

This is the part that's meaningfully different from most "template only"
site builders, and worth its own explanation for content purposes:

- **Payment methods are opt-in and real.** A merchant explicitly connects
  COD and/or Manual payment in their dashboard; checkout only ever shows
  what's actually connected — never a hardcoded default that implies a
  payment method exists when it doesn't.
- **Manual payment has a real verification trail.** The customer enters
  a transaction ID at checkout (required, validated), which the merchant
  sees directly on the order — this is the actual mechanism most BD
  merchants already use informally (send money, screenshot proof), just
  made structured instead of living in a chat thread.
- **Fraud protection works with zero historical data**, which matters
  because most merchants on this platform are new stores. Instead of a
  fake "risk score" that can't mean anything without order history, it's
  a merchant-maintained blocklist plus rules that evaluate only the
  current order.
- **Courier integration is a real API connection** (Steadfast today,
  verified against their live API at connect time — a wrong key fails
  immediately with a clear message, not a silent "saved" state that
  breaks later).

## 8. Pricing (current draft — confirm before publishing)

| | Starter | Growth | Business | Custom |
|---|---|---|---|---|
| Price | ৳1,190/mo | ৳2,990/mo | ৳6,990/mo | Talk to us |
| Products | 50 | 500 | Unlimited | Unlimited |
| Courier integrations | 1 | All | All | Whatever they use |
| Live payment gateway | — | Roadmap | Roadmap, priority | Negotiable |
| AI credits/month | 0 | 80 | 250 | Negotiable |
| Fraud protection | Blocklist only | Blocklist + rules | Blocklist + rules | Full |
| Branding | Shown | Removed | Removed | Removed |
| Support | Email | Priority email | Phone + onboarding | Dedicated |

AI credits are a real, purchasable unit (not a vague "AI included" claim) —
extra credits can be bought in packs (50/$5, 120/$10, 300/$22, 750/$45).
Custom domains are supported by the platform technically, but domain
*registration* itself is not something Softune sells or subsidizes — that's
a separate cost from any registrar, same as it would be anywhere else.

## 9. What's NOT Built Yet (important — don't let content overclaim)

Being explicit about this so blog/marketing content never promises
something that doesn't work if a prospective customer tries it:

- **No live payment gateway checkout** (bKash/Nagad/SSLCommerz/Rocket) —
  credentials can be stored, but there's no functioning auto-checkout
  through any of them yet.
- **No subscription billing enforcement** — the pricing tiers above are a
  business plan, not yet wired into the product (no plan field on tenants,
  no feature-gating by tier, no payment collection from merchants
  themselves).
- **AI credits are still a mock UI** — the dashboard shows a credits
  balance and a top-up modal, but there's no real balance-tracking or
  payment collection behind it yet; today's AI usage is a simple daily cap,
  not a per-tier credit system.
- **Pathao and RedX courier connections** aren't live (UI/schema exists,
  API integration doesn't).
- **No multi-staff/team accounts** — one login per tenant today.
- **No real customer accounts on the storefront side** — checkout is
  guest-only; customers can't log in to view past orders.
- **Funnels/upsell flows** are a deliberately postponed feature, not
  started.
- **No analytics on storefront visits/traffic** — only order-derived
  metrics exist; there is no visit-tracking system, so anything like
  "conversion rate" isn't something the product can honestly report yet.

## 10. Suggested Angles for Blog Content

A few starting threads, given everything above — expand or discard freely:

- "Why COD-first checkout isn't a lesser feature — it's the actual BD
  market" (contrast with gateway-first builders like Shopify).
- "What agencies charge you ৳30,000+ for, and why that shouldn't be a
  one-time unrecoverable cost."
- "Fraud protection without order history — why 'AI risk scoring' is
  mostly marketing for a brand-new store."
- "The real cost of 'free' website builders" (hosting, plugin sprawl,
  agency lock-in for every future change).
- Product-led pieces: theme editor walkthrough, courier connection
  walkthrough, what happens behind the scenes when a customer checks out
  (server-side price recomputation, fraud check, notification, all in one
  request).
- Merchant-education pieces (not really about Softune directly, but good
  for SEO/traffic): "How to price delivery charges by location in
  Bangladesh," "Manual bKash payment verification best practices,"
  "Reading your store's best-seller data to restock smarter."
