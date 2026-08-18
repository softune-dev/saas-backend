-- =============================================================================
--  007_aurora_template.sql — register the Aurora storefront template
-- =============================================================================
--  Run AFTER 006_inquiries.sql. Safe to re-run (ON CONFLICT ... DO UPDATE).
--
--  WHY THIS EXISTS
--  Templates 001-005 seeded the original block-registry demos (restaurant-01,
--  saas-landing-01, portfolio-01, agency-01). None of them correspond to the
--  Aurora storefront in templates/aurora, so POST /sites had no template row to
--  create an Aurora site from. This adds it.
--
--  TWO TEMPLATE FAMILIES NOW COEXIST — this is deliberate, not drift:
--
--    * BLOCK-REGISTRY templates (restaurant-01, agency-01, …) describe a page as
--      an array of {type, data} blocks validated by app/blocks.py. Their section
--      names are PascalCase ('Hero', 'MenuList') because that is the registry's
--      vocabulary, and their starter content lives in `default_pages`.
--
--    * CONTRACT templates (aurora) render a fixed set of sections defined by
--      templates/aurora/lib/theme-types.ts. Their section names are camelCase
--      ('hero', 'featureProducts') and ALL starter content — including the page
--      list and section order — lives inside one `default_theme` JSON blob,
--      because that blob IS the SiteEditorSettings object the template reads.
--
--  Aurora uses BOTH columns, for two different jobs — this is not duplication:
--
--    * default_theme.pages  → the page list the DASHBOARD EDITOR renders in its
--      page panel (id / type / title / path / enabled). Editor concern only.
--
--    * default_pages        → becomes real rows in `site_pages`, which is what
--      holds PER-PAGE SEO (title, meta_description, og_image, noindex) and what
--      GET /public/site/{host} reads to build `nav`, `pages` and the sitemap.
--
--  Populating default_pages costs no new backend code: POST /sites already
--  loops over it and inserts a SitePage per entry. Leaving it empty would mean
--  every page shared one site-level meta description — a real SEO problem, and
--  unfixable from the theme blob because SiteEditorSettings.SitePage has no
--  `seo` field at all.
--
--  THE POINT OF THE SEED
--  POST /sites copies default_theme into sites.theme. That means a brand-new
--  customer site arrives pre-filled with editable starter content, served from
--  the DATABASE rather than hardcoded in the template's source. This is the
--  first step of removing templates/aurora/lib/sample-data.ts entirely.
--
--  ON CONFLICT DO UPDATE (not DO NOTHING) is intentional here: while the Aurora
--  template is still being designed, re-running this file should refresh the
--  starter content rather than silently keep a stale copy. Existing customer
--  sites are unaffected — they hold their own copy in sites.theme.
-- =============================================================================

INSERT INTO templates (
    key,
    name,
    description,
    framework,
    block_types,
    price_cents,
    is_active,
    default_theme,
    default_pages
)
VALUES (
    'aurora',
    'Aurora',
    'Editorial fashion and lifestyle storefront. Serif display type, full-bleed photography, and a quiet, considered layout.',
    'nextjs',
    -- Section types Aurora actually implements, read from its SectionType union
    -- (templates/aurora/lib/theme-types.ts). camelCase on purpose — see header.
    ARRAY[
        'banner',
        'hero',
        'categories',
        'featureProducts',
        'productShowcase',
        'whyChooseUs',
        'features',
        'testimonials',
        'bannerCta',
        'footer'
    ],
    0,
    true,

    -- default_theme = the complete SiteEditorSettings object, transcribed from
    -- templates/aurora/lib/sample-data.ts `defaultSettings`. Field names must
    -- match exactly; the template reads these keys directly.
    '{
        "siteName": "Maison",
        "tagline": "Quiet, considered garments made to last",
        "primaryColor": "#1c1917",
        "accentColor": "#78716c",
        "surfaceColor": "#faf9f6",
        "fontFamily": "Inter",
        "buttonStyle": "Square",
        "headerStyle": "Minimal",
        "navLinks": [
            { "id": "nav-1", "label": "Shop",  "path": "/shop" },
            { "id": "nav-2", "label": "Women", "path": "/shop?category=women" },
            { "id": "nav-3", "label": "Men",   "path": "/shop?category=men" },
            { "id": "nav-4", "label": "About", "path": "/about" }
        ],
        "headerButtons": [],
        "pages": [
            { "id": "page-1",  "type": "home",          "title": "Home",            "path": "/",           "enabled": true },
            { "id": "page-2",  "type": "products",      "title": "Shop",            "path": "/shop",       "enabled": true },
            { "id": "page-3",  "type": "categories",    "title": "Categories",      "path": "/categories", "enabled": true },
            { "id": "page-4",  "type": "cart",          "title": "Cart",            "path": "/cart",       "enabled": true },
            { "id": "page-5",  "type": "checkout",      "title": "Checkout",        "path": "/checkout",   "enabled": true },
            { "id": "page-6",  "type": "about",         "title": "About",           "path": "/about",      "enabled": true },
            { "id": "page-7",  "type": "contact",       "title": "Contact",         "path": "/contact",    "enabled": true },
            { "id": "page-8",  "type": "faq",           "title": "FAQ",             "path": "/faq",        "enabled": true },
            { "id": "page-9",  "type": "privacy",       "title": "Privacy Policy",  "path": "/privacy",    "enabled": true },
            { "id": "page-10", "type": "terms",         "title": "Terms",           "path": "/terms",      "enabled": true },
            { "id": "page-11", "type": "productDetail", "title": "Product",         "path": "/shop/:slug", "enabled": true },
            { "id": "page-12", "type": "notFound",      "title": "Page Not Found",  "path": "/404",        "enabled": true }
        ],
        "sections": [
            { "id": "sec-1", "type": "hero" },
            { "id": "sec-2", "type": "banner" },
            { "id": "sec-3", "type": "categories" },
            { "id": "sec-4", "type": "featureProducts" },
            { "id": "sec-5", "type": "productShowcase" },
            { "id": "sec-6", "type": "testimonials" },
            { "id": "sec-7", "type": "features" },
            { "id": "sec-8", "type": "footer" }
        ],
        "announcementText": "New Season ✦ Free Shipping Over ৳2,500 ✦ Crafted to Last",
        "heroEyebrow": "Autumn / Winter 26",
        "heroTitle": "Quiet luxury.",
        "heroBody": "Garments cut for a slower pace — in cashmere, silk and softly tailored wool.",
        "heroCta": "Shop the collection",
        "heroSecondary": "Lookbook",
        "categoriesTitle": "The collections.",
        "selectedCategoryIds": ["cat-1", "cat-2", "cat-3", "cat-4", "cat-5"],
        "featureProductsTitle": "New arrivals.",
        "selectedProductIds": ["prod-1", "prod-2", "prod-3", "prod-4", "prod-5", "prod-6"],
        "showcaseTitle": "001 — The Coat",
        "showcaseBody": "Tailored in Italy with a relaxed shoulder, unstructured collar and deep welt pockets. Cut from virgin wool and cashmere.",
        "showcaseCta": "Add to bag",
        "showcaseProductId": "prod-1",
        "whyTitle": "Why customers trust Maison",
        "why1": "Worldwide delivery on all orders. Delivered in signature packaging.",
        "why2": "Our garments are made to last with complimentary repairs.",
        "why3": "Every fiber is ethically sourced from family-run mills.",
        "featuresTitle": "Our Commitments",
        "feature1": "Worldwide delivery and returns on all orders over ৳2,500. Delivered in signature sustainable packaging.",
        "feature2": "Our garments are made to last. We offer care guides and repairs to keep your pieces in perfect condition.",
        "feature3": "Every fiber is ethically sourced. We partner exclusively with family-run master craft clusters.",
        "testimonialsTitle": "What our clients say",
        "testimonials": [
            {
                "id": "test-1",
                "name": "Camille Laurent",
                "quote": "The texture and drape of the virgin wool jacket are exceptional. Clean, quiet luxury at its very best.",
                "image": "/assets/women/1.jpg",
                "role": "Paris, France",
                "rating": 5
            },
            {
                "id": "test-2",
                "name": "Marcus Vance",
                "quote": "The linen overshirt is my favorite staple. The construction, weight, and cut are genuinely heirloom quality.",
                "image": "/assets/men/1.jpg",
                "role": "London, UK",
                "rating": 5
            },
            {
                "id": "test-3",
                "name": "Elena Rostova",
                "quote": "The structured leather bag feels bespoke. Delivered swiftly in immaculate packaging.",
                "image": "/assets/bags/1.jpg",
                "role": "Milan, Italy",
                "rating": 5
            },
            {
                "id": "test-4",
                "name": "Arthur Pendelton",
                "quote": "The cashmere knitwear is incredibly warm yet lightweight. Exceptionally tailored.",
                "image": "/assets/men/2.jpg",
                "role": "New York, USA",
                "rating": 5
            },
            {
                "id": "test-5",
                "name": "Sonia Gauthier",
                "quote": "Minimalist aesthetics combined with supreme quality. Highly recommend their bespoke tailoring.",
                "image": "/assets/women/2.jpg",
                "role": "Geneva, Switzerland",
                "rating": 5
            }
        ],
        "ctaTitle": "Join the Maison Journal",
        "ctaBody": "Subscribe for private previews, seasonal lookbooks, and quiet stories.",
        "ctaButton": "Shop Now",
        "footerText": "© 2026 Maison. All rights reserved.",
        "footerLink": "/privacy"
    }'::jsonb,

    -- Becomes one `site_pages` row per entry via POST /sites. `blocks` stays
    -- empty for every page — Aurora renders from real Next.js routes, not from
    -- the block registry. These rows exist to carry EDITABLE PER-PAGE SEO.
    --
    -- Only ADDRESSABLE pages are listed. Deliberately excluded:
    --   * productDetail (/shop/:slug) — a dynamic route, not one fixed page.
    --     Its per-product SEO comes from the product record instead.
    --   * notFound (/404) — never indexed, never in a sitemap.
    --
    -- cart and checkout carry noindex: they are functional, not content, and a
    -- half-filled cart ranking in Google helps nobody.
    '[
        {
            "slug": "",
            "title": "Home",
            "blocks": [],
            "seo": {
                "meta_description": "Quiet, considered garments made to last — cashmere, silk and softly tailored wool for a slower pace."
            }
        },
        {
            "slug": "shop",
            "title": "Shop",
            "blocks": [],
            "seo": {
                "meta_description": "Browse the full collection — coats, knitwear, tailoring, bags and accessories, crafted to last."
            }
        },
        {
            "slug": "categories",
            "title": "Categories",
            "blocks": [],
            "seo": {
                "meta_description": "Explore the collections by category — womenswear, menswear, bags, shoes and accessories."
            }
        },
        {
            "slug": "cart",
            "title": "Cart",
            "blocks": [],
            "seo": { "noindex": true }
        },
        {
            "slug": "checkout",
            "title": "Checkout",
            "blocks": [],
            "seo": { "noindex": true }
        },
        {
            "slug": "about",
            "title": "About",
            "blocks": [],
            "seo": {
                "meta_description": "Our story — ethically sourced fibers, family-run mills, and garments made to be repaired rather than replaced."
            }
        },
        {
            "slug": "contact",
            "title": "Contact",
            "blocks": [],
            "seo": {
                "meta_description": "Get in touch about an order, sizing, or a bespoke request. We reply within one business day."
            }
        },
        {
            "slug": "faq",
            "title": "FAQ",
            "blocks": [],
            "seo": {
                "meta_description": "Answers on delivery, returns, sizing, garment care and repairs."
            }
        },
        {
            "slug": "privacy",
            "title": "Privacy Policy",
            "blocks": [],
            "seo": {
                "meta_description": "How we collect, use and protect your personal data."
            }
        },
        {
            "slug": "terms",
            "title": "Terms",
            "blocks": [],
            "seo": {
                "meta_description": "Terms of service, delivery terms and returns policy."
            }
        }
    ]'::jsonb
)
ON CONFLICT (key) DO UPDATE SET
    name          = EXCLUDED.name,
    description   = EXCLUDED.description,
    framework     = EXCLUDED.framework,
    block_types   = EXCLUDED.block_types,
    price_cents   = EXCLUDED.price_cents,
    is_active     = EXCLUDED.is_active,
    default_theme = EXCLUDED.default_theme,
    default_pages = EXCLUDED.default_pages;
