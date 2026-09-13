-- =============================================================================
--  063_fashion_classic_template.sql — register the Fashion Classic template
-- =============================================================================
--  Run AFTER 062_event_popup.sql.
--
--  WHY THIS EXISTS
--  Fashion Classic (templates/fashion-classic, deployed as saas-theme3) is a
--  new storefront skin — a sharp-cornered, centered/bold-primary redesign
--  forked from Aurora. It implements the exact same SectionType/page contract
--  Aurora does (same templates/aurora/lib/theme-types.ts, copied verbatim into
--  the new repo), so this row is transcribed key-for-key from Aurora's entry
--  in 008_bazaar_template.sql — no new default_theme fields, no new section
--  types. POST /sites has no template row to create a Fashion Classic site
--  from until this runs.
--
--  Blank/structural-only default_theme, same convention 008 established for
--  Bazaar: colors, fonts, button style, nav/footer link skeletons, page list
--  and section order are populated because the dashboard editor needs a
--  starting value to render at all; anything that would be a fabricated
--  business claim (testimonials, why-choose-us copy, feature copy, tagline,
--  footer description, hero images, selected category/product ids) is blank.
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
    'fashion-classic',
    'Fashion Classic',
    'Sharp-cornered, centered/bold-primary fashion storefront — a structured counterpart to Aurora''s softer editorial look.',
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
        "buttonStyle": "Square",
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
