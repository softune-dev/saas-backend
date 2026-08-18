-- =============================================================================
--  008_bazaar_template.sql — register Bazaar, and fix Aurora's stale seed
-- =============================================================================
--  Run AFTER 007_aurora_template.sql.
--
--  WHY THIS EXISTS
--  Two problems, fixed together since both are "the templates table doesn't
--  match reality":
--
--  1. Only Aurora has a `templates` row. Bazaar (templates/bazaar) has no
--     entry, so POST /sites can never create a Bazaar site.
--
--  2. Aurora's row (from 007) was written against an OLD SiteEditorSettings
--     shape (fontFamily, headerStyle, heroEyebrow, heroTitle, ...) that no
--     longer matches templates/aurora/lib/theme-types.ts or the dashboard
--     editor's current schema (displayFont/bodyFont, feature1Title/
--     feature1IconKind/..., footerShopLinks, ...). A site created from that
--     stale blob would load into the editor with most fields simply
--     `undefined` — theme-editor-view.tsx casts the remote theme straight
--     into SiteEditorSettings with no backfill on that path.
--
--  Per repo convention, 007 (already run) is never edited — this is a new
--  migration that re-registers 'aurora' via the same ON CONFLICT DO UPDATE
--  007 already used, plus a fresh INSERT for 'bazaar'.
--
--  CONTENT IS DELIBERATELY BLANK, NOT DEMO DATA
--  Every field here matches today's real SiteEditorSettings key-for-key (see
--  dashboard/components/themes/editor/editor-types.ts and
--  dashboard/components/themes/editor/editor-defaults.ts, which this was
--  transcribed from), but every field that would otherwise be a fabricated
--  business claim — testimonials, "why choose us" copy, feature copy,
--  tagline, footer description, hero images, selected category/product ids —
--  is blank. A brand-new site must not launch with sentences the merchant
--  never wrote. Structural fields (colors, fonts, button style, nav/footer
--  link skeletons, page list, section order) stay populated because the
--  editor genuinely needs a starting value for those to render at all.
-- =============================================================================

-- ---------------------------------------------------------------------------
--  Shared page list (identical for both templates — same pageCatalog the
--  dashboard editor itself uses; see editor-types.ts pageCatalog/defaultPages)
-- ---------------------------------------------------------------------------
-- (inlined per-template below since Postgres has no JSON variables in plain SQL)

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
    ARRAY[
        'banner', 'hero', 'categories', 'featureProducts', 'productShowcase',
        'categoryShowcase', 'whyChooseUs', 'features', 'testimonials',
        'bannerCta', 'footer'
    ],
    0,
    true,

    '{
        "siteName": "My Store",
        "logoType": "text",
        "logoImage": "",
        "tagline": "",
        "primaryColor": "#FF5A36",
        "accentColor": "#171717",
        "surfaceColor": "#F4F4F5",
        "displayFont": "fraunces",
        "bodyFont": "inter",
        "buttonStyle": "Pill",
        "navLinks": [
            { "id": "n1", "label": "Shop", "path": "/shop" },
            { "id": "n2", "label": "About", "path": "/about" },
            { "id": "n3", "label": "Contact", "path": "/contact" }
        ],
        "headerButtons": [{ "id": "b1", "label": "Cart", "style": "primary" }],
        "pages": [
            { "id": "p1",  "type": "home",          "title": "Home",              "path": "/",           "enabled": true },
            { "id": "p2",  "type": "products",      "title": "Shop",              "path": "/shop",       "enabled": true },
            { "id": "p3",  "type": "productDetail",  "title": "Product details",  "path": "/shop/:slug", "enabled": true },
            { "id": "p4",  "type": "categories",    "title": "Categories",        "path": "/categories", "enabled": true },
            { "id": "p5",  "type": "cart",          "title": "Cart",              "path": "/cart",       "enabled": true },
            { "id": "p6",  "type": "checkout",      "title": "Checkout",          "path": "/checkout",   "enabled": true },
            { "id": "p7",  "type": "about",         "title": "About",             "path": "/about",      "enabled": true },
            { "id": "p8",  "type": "contact",       "title": "Contact",           "path": "/contact",    "enabled": true },
            { "id": "p9",  "type": "faq",           "title": "FAQ",               "path": "/faq",        "enabled": true },
            { "id": "p10", "type": "privacy",       "title": "Privacy",           "path": "/privacy",    "enabled": true },
            { "id": "p11", "type": "terms",         "title": "Terms",             "path": "/terms",      "enabled": true },
            { "id": "p12", "type": "notFound",      "title": "404",               "path": "/404",        "enabled": true }
        ],
        "sections": [
            { "id": "s1", "type": "banner" },
            { "id": "s2", "type": "hero" },
            { "id": "s3", "type": "categories" },
            { "id": "s4", "type": "featureProducts" },
            { "id": "s5", "type": "productShowcase" },
            { "id": "s5b", "type": "categoryShowcase" },
            { "id": "s6", "type": "whyChooseUs" },
            { "id": "s7", "type": "features" },
            { "id": "s8", "type": "testimonials" },
            { "id": "s9", "type": "bannerCta" },
            { "id": "s10", "type": "footer" }
        ],
        "announcementItems": [],
        "announcementDivider": "✦",
        "heroImages": [],
        "heroImagesSquare": [],
        "categoriesTitle": "Shop by category",
        "selectedCategoryIds": [],
        "featureProductsTitle": "Featured products",
        "selectedProductIds": [],
        "showcaseProductId": "",
        "whyTitle": "Why choose us",
        "whyImage": "",
        "why1Title": "", "why1": "",
        "why2Title": "", "why2": "",
        "why3Title": "", "why3": "",
        "categoryShowcaseTitle": "Shop by collection",
        "categoryShowcaseCategoryIds": [],
        "featuresTitle": "Features",
        "feature1Title": "", "feature1": "", "feature1IconKind": "icon", "feature1Icon": "leaf", "feature1Image": "",
        "feature2Title": "", "feature2": "", "feature2IconKind": "icon", "feature2Icon": "shield-check", "feature2Image": "",
        "feature3Title": "", "feature3": "", "feature3IconKind": "icon", "feature3Icon": "package", "feature3Image": "",
        "testimonialsTitle": "What customers say",
        "testimonials": [],
        "ctaTitle": "", "ctaBody": "", "ctaButton": "",
        "footerDescription": "",
        "footerShopLabel": "Shop",
        "footerShopLinks": [{ "id": "fs1", "label": "All products", "path": "/shop" }],
        "footerCompanyLabel": "Company",
        "footerCompanyLinks": [
            { "id": "fc1", "label": "About", "path": "/about" },
            { "id": "fc2", "label": "Contact", "path": "/contact" }
        ]
    }'::jsonb,

    -- Addressable pages only (productDetail is dynamic, notFound is never
    -- indexed) — no fabricated meta_description; the merchant fills SEO in
    -- from Site Settings once they have real copy. cart/checkout stay
    -- noindex because that is a functional necessity, not content.
    '[
        { "slug": "",          "title": "Home",       "blocks": [], "seo": {} },
        { "slug": "shop",      "title": "Shop",       "blocks": [], "seo": {} },
        { "slug": "categories","title": "Categories", "blocks": [], "seo": {} },
        { "slug": "cart",      "title": "Cart",       "blocks": [], "seo": { "noindex": true } },
        { "slug": "checkout",  "title": "Checkout",   "blocks": [], "seo": { "noindex": true } },
        { "slug": "about",     "title": "About",      "blocks": [], "seo": {} },
        { "slug": "contact",   "title": "Contact",    "blocks": [], "seo": {} },
        { "slug": "faq",       "title": "FAQ",         "blocks": [], "seo": {} },
        { "slug": "privacy",   "title": "Privacy",     "blocks": [], "seo": {} },
        { "slug": "terms",     "title": "Terms",       "blocks": [], "seo": {} }
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
    'bazaar',
    'Bazaar',
    'Multi-category marketplace storefront. Department navigation, deal-driven hero, and a catalog-first layout for stores that sell everything.',
    'nextjs',
    ARRAY[
        'hero', 'features', 'categories', 'featureProducts', 'whyChooseUs', 'footer'
    ],
    0,
    true,

    '{
        "siteName": "My Store",
        "logoType": "text",
        "logoImage": "",
        "tagline": "",
        "primaryColor": "#2563EB",
        "accentColor": "#0F172A",
        "surfaceColor": "#F8FAFC",
        "displayFont": "outfit",
        "bodyFont": "inter",
        "buttonStyle": "Rounded",
        "navLinks": [
            { "id": "nav-1", "label": "Home", "path": "/" },
            { "id": "nav-2", "label": "Shop", "path": "/shop" },
            { "id": "nav-3", "label": "Contact", "path": "/contact" }
        ],
        "headerButtons": [],
        "pages": [
            { "id": "p1",  "type": "home",          "title": "Home",              "path": "/",           "enabled": true },
            { "id": "p2",  "type": "products",      "title": "Shop",              "path": "/shop",       "enabled": true },
            { "id": "p3",  "type": "productDetail",  "title": "Product details",  "path": "/shop/:slug", "enabled": true },
            { "id": "p4",  "type": "categories",    "title": "Categories",        "path": "/categories", "enabled": true },
            { "id": "p5",  "type": "cart",          "title": "Cart",              "path": "/cart",       "enabled": true },
            { "id": "p6",  "type": "checkout",      "title": "Checkout",          "path": "/checkout",   "enabled": true },
            { "id": "p7",  "type": "about",         "title": "About",             "path": "/about",      "enabled": true },
            { "id": "p8",  "type": "contact",       "title": "Contact",           "path": "/contact",    "enabled": true },
            { "id": "p9",  "type": "faq",           "title": "FAQ",               "path": "/faq",        "enabled": true },
            { "id": "p10", "type": "privacy",       "title": "Privacy",           "path": "/privacy",    "enabled": true },
            { "id": "p11", "type": "terms",         "title": "Terms",             "path": "/terms",      "enabled": true },
            { "id": "p12", "type": "notFound",      "title": "404",               "path": "/404",        "enabled": true }
        ],
        "sections": [
            { "id": "sec-1", "type": "hero" },
            { "id": "sec-2", "type": "features" },
            { "id": "sec-3", "type": "categories" },
            { "id": "sec-4", "type": "featureProducts" },
            { "id": "sec-5", "type": "whyChooseUs" },
            { "id": "sec-6", "type": "footer" }
        ],
        "announcementItems": [],
        "announcementDivider": "·",
        "heroImages": [],
        "heroImagesSquare": [],
        "categoriesTitle": "Shop by Category",
        "selectedCategoryIds": [],
        "featureProductsTitle": "Best Sellers",
        "selectedProductIds": [],
        "showcaseProductId": "",
        "whyTitle": "Why shop with us",
        "whyImage": "",
        "why1Title": "", "why1": "",
        "why2Title": "", "why2": "",
        "why3Title": "", "why3": "",
        "categoryShowcaseTitle": "Popular departments",
        "categoryShowcaseCategoryIds": [],
        "featuresTitle": "Why customers trust us",
        "feature1Title": "", "feature1": "", "feature1IconKind": "icon", "feature1Icon": "package", "feature1Image": "",
        "feature2Title": "", "feature2": "", "feature2IconKind": "icon", "feature2Icon": "tag", "feature2Image": "",
        "feature3Title": "", "feature3": "", "feature3IconKind": "icon", "feature3Icon": "truck", "feature3Image": "",
        "testimonialsTitle": "",
        "testimonials": [],
        "ctaTitle": "", "ctaBody": "", "ctaButton": "",
        "footerDescription": "",
        "footerShopLabel": "Shop",
        "footerShopLinks": [
            { "id": "fs1", "label": "All products", "path": "/shop" },
            { "id": "fs2", "label": "Categories", "path": "/categories" }
        ],
        "footerCompanyLabel": "Company",
        "footerCompanyLinks": [
            { "id": "fc1", "label": "About", "path": "/about" },
            { "id": "fc2", "label": "Contact", "path": "/contact" },
            { "id": "fc3", "label": "FAQ", "path": "/faq" },
            { "id": "fc4", "label": "Privacy", "path": "/privacy" },
            { "id": "fc5", "label": "Terms", "path": "/terms" }
        ]
    }'::jsonb,

    '[
        { "slug": "",          "title": "Home",       "blocks": [], "seo": {} },
        { "slug": "shop",      "title": "Shop",       "blocks": [], "seo": {} },
        { "slug": "categories","title": "Categories", "blocks": [], "seo": {} },
        { "slug": "cart",      "title": "Cart",       "blocks": [], "seo": { "noindex": true } },
        { "slug": "checkout",  "title": "Checkout",   "blocks": [], "seo": { "noindex": true } },
        { "slug": "about",     "title": "About",      "blocks": [], "seo": {} },
        { "slug": "contact",   "title": "Contact",    "blocks": [], "seo": {} },
        { "slug": "faq",       "title": "FAQ",         "blocks": [], "seo": {} },
        { "slug": "privacy",   "title": "Privacy",     "blocks": [], "seo": {} },
        { "slug": "terms",     "title": "Terms",       "blocks": [], "seo": {} }
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
