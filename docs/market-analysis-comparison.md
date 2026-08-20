# Softune — Market Analysis for the Landing Page Comparison Section

Deep-dive research to ground the landing page's Comparison section in real,
verifiable data instead of generic self-praise. Covers Softune's real plan
mechanics plus researched competitor data — both Bangladesh-native store
builders (Softune's real market) and the two global platforms every BD
merchant has heard of (Shopify, WooCommerce), since those are the fallback
choice a prospect compares against even outside BD.

**How to use this doc:** feed it directly to whatever tool builds the
Comparison section content next — it has real numbers, real sources, and an
explicit "don't say this" list for anything that couldn't be verified.

---

## 1. Softune's real numbers (ground truth — pull from here, not from memory)

### Plans & AI credits (`app/ai.py` — `PLAN_AI_DAILY_CAP`)
| Plan | AI credits / day |
|---|---|
| Demo | 50 |
| Starter | **0** (no AI on Starter) |
| Growth | 80 |
| Business | 250 |

### Plans & storage (`app/media.py` — `PLAN_STORAGE_LIMIT_BYTES`)
| Plan | Storage |
|---|---|
| Demo | 800 MB |
| Starter | 500 MB |
| Growth | 2 GB |
| Business | 5 GB |

### Current advertised pricing (`landing/components/pricing.tsx`)
| Plan | Monthly | Annual (per mo) | Products |
|---|---|---|---|
| Starter | ৳1,190 | ৳950 | 50 |
| Growth | ৳2,990 | ৳2,390 | 500 |
| Business | ৳6,990 | ৳5,590 | Unlimited |

All three: **0% transaction fee** — this is a genuine differentiator (see
Shopify/WooCommerce transaction-fee data below) and should be a headline
comparison point, not a footnote.

### What's actually built (for the "features" side of any comparison table)
- Multi-tenant dashboard: Products, Categories, Orders, Analytics (+
  CSV/PDF/JSON export), Customers, Courier, Payments, Fraud Protection,
  Billing, Site Settings (business info, FAQs, legal pages, custom domain,
  shipping, media library).
- **Multiple real storefront themes** (Aurora, Bazaar, Sweets) with a live
  visual theme editor — brand/colors/header/pages/sections, AI Suggest for
  brand & color direction, live iframe preview, one-click publish.
- **AI Assistant** across the dashboard (chat sidebar; writes/edits products
  & categories with an explicit confirm-before-write step; AI Suggest inside
  the theme editor) — metered per plan (table above), not unlimited, and
  disclosed as AI-generated with "AI can make mistakes"-style disclaimers.
- **Add-Ons marketplace**: Payments and Courier are included by default; 16
  additional requestable add-ons across Customer Engagement, Marketing &
  Sales, AI Automation, and Operations & Insights.
- Guided onboarding: a Getting Started setup checklist plus an interactive
  in-app product tour that walks through the dashboard AND the theme editor.
- Dark mode, mobile-responsive dashboard.
- BD-native by default: local payment gateway support (bKash-class) and
  courier integrations, ৳-denominated pricing.

### Honest gaps (don't claim these — flag as known limitations, useful if the
content needs a "how we compare on X" line that shouldn't overclaim)
- Starter plan has **zero** AI credits — don't market "AI on every plan"
  without qualifying it.
- Single custom domain per site; no built-in multi-currency; no native POS
  hardware integration (some BD competitors — Shopstick — bundle POS).

---

## 2. Global platforms (the "default" comparison point outside BD)

### Shopify (2026 pricing, via public sources — see Sources)
| Plan | Price/mo | Transaction fee (Shopify Payments) |
|---|---|---|
| Basic | $39 ($29 annual) | 2.9% + $0.30 (~$3.20 per $100) |
| Grow | $79–105 | ~3.0% per $100 |
| Advanced | $399 | ~2.8% per $100 |
| Plus | $2,300+ | ~2.45% per $100 (negotiated) |

- Using a non-Shopify payment gateway adds a further 0.2%–2.0% surcharge on
  top of the gateway's own fee.
- Apps commonly add **$350–$1,400/month** on top for a mid-market store.
- No BD-native payment gateway or courier integration out of the box —
  needs third-party apps, which stack additional monthly fees.
- **Real comparison angle for Softune:** Shopify charges a real percentage
  of every sale on top of the subscription; Softune's 0% transaction fee is
  a genuine, quantifiable saving at any real sales volume.

### WooCommerce
- The plugin itself is free, but it is **not** a SaaS product — it's
  self-hosted WordPress. Real total cost is assembled from hosting
  ($5–$300+/mo), SSL, premium extensions, and dev time.
- Sourced estimates: small store $75–$920/yr, mid-size $1,420–$6,550/yr,
  production-ready $1,800–$15,000+/yr.
- Payment processing still costs 2.9% + $0.30 per US-card transaction (plus
  1–1.5% cross-border fee on international cards).
- **Real comparison angle:** WooCommerce's "free plugin" framing hides a
  real, variable, often-higher total cost and requires ongoing technical
  maintenance (hosting, plugin updates, security) that a SaaS platform like
  Softune doesn't ask a merchant to own.

---

## 3. Bangladesh-native competitors (Softune's real, direct market)

### ShopZero (shopzero.bd) — closest direct competitor on structure
- Pricing: Hobbyist ৳999/mo, Growth ৳2,499/mo, Scale ৳5,999/mo — all with a
  14-day trial **and a one-time ৳5,000 setup fee**.
- Hobbyist/Growth = 1 store; Scale = up to 3 stores.
- Supports bKash, SSLCommerz, Moneybag, EPS (payments) and Pathao, Steadfast,
  RedX, eCourier (delivery) — explicitly states "we never charge per order."
- No AI-assistant or AI theme-editor positioning found in public material.
- **Real comparison angle:** Softune has no setup fee and includes an AI
  assistant + AI theme editor; ShopZero's per-plan pricing is broadly
  comparable but adds a ৳5,000 upfront cost Softune doesn't have.

### Bitcommerz — closest competitor on AI positioning
- Markets itself as "Bangladesh's #1 AI-Powered Store Builder": bKash & card
  payments, REDX courier, an AI product-description writer, real-time
  analytics. Claims 2,000+ merchants.
- 7-day free trial, no credit card required; also offers a permanent free
  tier to evaluate the platform.
- Exact tiered BDT pricing wasn't published in any indexed source — don't
  state specific Bitcommerz prices; if a number is needed, say "pricing not
  publicly listed" rather than inventing one.
- **Real comparison angle:** Bitcommerz's AI angle is narrower — an AI
  product-description writer — versus Softune's AI assistant across
  products, theme direction (AI Suggest), and category writes, all visible
  through one chat surface with an explicit confirm-before-write UX.

### Shopstick — POS + e-commerce combo, not primarily an AI/theme play
- Positions as all-in-one business management: POS, inventory, and a newer
  "E-Commerce Builder" module, usable on a free subdomain or a custom domain
  with theme personalization.
- Runs a referral program (up to ৳12,000/referral) — a growth-marketing
  signal, not a product feature to compare against.
- No AI-assistant or multi-theme storefront system found in public material.
- **Real comparison angle:** Shopstick's strength is POS + inventory for
  merchants who also sell offline; Softune's strength is the storefront
  itself (multiple real themes, AI-assisted editing) — different core bet,
  not strictly better/worse, so frame as "if you need online-first with a
  real design system + AI, that's Softune's lane."

### Other BD builders surfaced but not deeply comparable (mentioned for
completeness, insufficient public data to make specific claims)
- **DeshiCommerce**, **BD Commerce**, **Selldone (BD)** — all position as
  no-code / quick-launch BD store builders. None had public tiered pricing
  or a specific AI feature set surfaced in this research pass. If used in
  copy, keep claims generic ("some BD builders require X") rather than
  naming these platforms with unverified specifics.

---

## 4. Suggested comparison table structure (for the Comparison section)

A row-based "Softune vs. typical builder" table (safe, verified) is lower
risk than naming any single competitor, since exact competitor pricing
shifts and isn't fully public for several of them. Suggested rows, all
backed by data above:

| | Softune | Typical global SaaS (Shopify-class) | Typical BD SaaS builder |
|---|---|---|---|
| Transaction fee | 0% | ~2.8%–3.0% per sale | Varies, often per-order fee |
| Setup fee | None | None | Some charge one-time setup (e.g. ৳5,000) |
| AI assistant | Built-in, dashboard-wide (metered by plan) | Third-party apps only | Rare; usually limited to one AI writing tool if present at all |
| Storefront themes | Multiple real templates + live AI-assisted editor | Theme marketplace (paid) | Usually one fixed theme per merchant |
| BD payments/courier | Native | Requires apps | Native (this is table stakes in BD) |
| Add-ons marketplace | 16 built-in, categorized | Large app store (cost stacks) | Limited or none |

If naming Shopify/WooCommerce specifically is wanted later, the numbers in
section 2 are sourced and safe to attribute directly to them by name. Do
**not** name ShopZero, Bitcommerz, or Shopstick by name in the public-facing
comparison table unless the pricing/feature claim is explicitly verified
above — general/anonymized "typical BD builder" framing is safer for
anything not in that verified list.

---

## Sources

- [Shopify Pricing: Plans, Fees, and All Hidden Costs 2026](https://litextension.com/blog/shopify-pricing/)
- [Shopify Fees 2026: Every Cost Per Sale, Transaction, and Plan Explained](https://qualimero.com/en/blog/shopify-fees)
- [WooCommerce Pricing in 2026: Full Cost Breakdown](https://www.swell.is/content/woocommerce-pricing)
- [WooCommerce Pricing: How Much It Really Costs in 2026](https://www.omnisend.com/blog/woocommerce-pricing/)
- [Best E-Commerce Platforms for Bangladesh 2026 — ShopZero Blog](https://shopzero.bd/blog/best-ecommerce-platforms-bangladesh-2026)
- [ShopZero — E-commerce with Zero Friction for BD Sellers](https://shopzero.bd/)
- [Bitcommerz — Bangladesh's #1 AI-Powered Store Builder](https://bitcommerz.com/)
- [Bitcommerz Terms and Conditions](https://bitcommerz.com/terms-and-condition/)
- [Shopstick Pricing for Businesses](https://shopstick.com.bd/pricing)
- [Shopstick E-Commerce Builder](https://shopstick.com.bd/e-commerce-builder)
- [20 Best Ecommerce Software in Bangladesh (2026) — SoftwareSuggest](https://www.softwaresuggest.com/e-commerce-software/bangladesh)
- [Top ecommerce platforms for Bangladeshi businesses 2026 — wemisc](https://wemisc.com/top-ecommerce-platforms-for-bangladeshi-businesses-2026/)
