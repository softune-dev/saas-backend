-- =============================================================================
--  004_seed.sql — demo template catalogue
-- =============================================================================
--  Run AFTER 003_commerce.sql. Safe to re-run (ON CONFLICT DO NOTHING).
--
--  Only TEMPLATES are seeded here. Tenants, users and sites are created through
--  the API instead (POST /auth/register, then POST /sites), because passwords
--  must be hashed by the application — never pasted as plain text into SQL.
--
--  The default_pages JSON below is also the clearest specification of the block
--  format. Compare it against app/blocks.py, which validates exactly this shape.
-- =============================================================================

INSERT INTO templates (key, name, description, framework, block_types, price_cents, default_theme, default_pages)
VALUES
(
    'restaurant-01',
    'Bistro',
    'Warm single-page restaurant site with menu and reservations.',
    'nextjs',
    ARRAY['Hero', 'About', 'MenuList', 'Gallery', 'Testimonials', 'ContactForm', 'Footer'],
    4900,
    '{
        "colors":  { "primary": "#8B4513", "accent": "#D2A24C", "bg": "#FFFDF8", "text": "#2B2118" },
        "fonts":   { "heading": "Playfair Display", "body": "Inter" },
        "radius":  "8px"
    }'::jsonb,
    '[
        {
            "slug": "",
            "title": "Home",
            "blocks": [
                { "type": "Hero", "data": {
                    "heading": "Slow food, fast smiles",
                    "subheading": "Wood-fired plates in the heart of downtown.",
                    "image_url": "",
                    "cta_text": "Book a table",
                    "cta_link": "#contact",
                    "align": "center"
                }},
                { "type": "About", "data": {
                    "heading": "Our story",
                    "body": "Family-run since 1998. Everything made from scratch.",
                    "image_url": ""
                }},
                { "type": "MenuList", "data": {
                    "heading": "Menu",
                    "items": [
                        { "name": "Margherita",   "description": "San Marzano, basil", "price_cents": 1400 },
                        { "name": "Truffle Pasta", "description": "Hand-rolled",        "price_cents": 2200 }
                    ]
                }},
                { "type": "ContactForm", "data": {
                    "heading": "Reservations",
                    "fields": [
                        { "name": "name",    "required": true  },
                        { "name": "email",   "required": true  },
                        { "name": "phone",   "required": false },
                        { "name": "message", "required": false }
                    ],
                    "submit_text": "Request a table"
                }},
                { "type": "Footer", "data": { "show_socials": true, "note": "" } }
            ],
            "seo": {
                "title": "Bistro — wood-fired dining downtown",
                "meta_description": "Family-run restaurant serving wood-fired plates since 1998."
            }
        }
    ]'::jsonb
),
(
    'saas-landing-01',
    'Launch',
    'Conversion-focused SaaS landing page with pricing and FAQ.',
    'nextjs',
    ARRAY['Hero', 'FeatureGrid', 'Testimonials', 'Pricing', 'FAQ', 'CTA', 'Footer'],
    5900,
    '{
        "colors":  { "primary": "#4F46E5", "accent": "#06B6D4", "bg": "#FFFFFF", "text": "#0F172A" },
        "fonts":   { "heading": "Inter", "body": "Inter" },
        "radius":  "12px"
    }'::jsonb,
    '[
        {
            "slug": "",
            "title": "Home",
            "blocks": [
                { "type": "Hero", "data": {
                    "heading": "Ship faster than your roadmap",
                    "subheading": "Everything you need, nothing you do not.",
                    "image_url": "",
                    "cta_text": "Start free",
                    "cta_link": "/signup",
                    "align": "left"
                }},
                { "type": "FeatureGrid", "data": {
                    "heading": "Why teams switch",
                    "columns": 3,
                    "items": [
                        { "icon": "zap",    "title": "Fast",   "body": "Sub-second everything." },
                        { "icon": "lock",   "title": "Secure", "body": "Isolated by default." },
                        { "icon": "puzzle", "title": "Simple", "body": "No setup calls." }
                    ]
                }},
                { "type": "Pricing", "data": {
                    "heading": "Pricing",
                    "tiers": [
                        { "name": "Free", "price_cents": 0,    "period": "month", "features": [{ "text": "1 site" }],                          "highlighted": false, "cta_text": "Start" },
                        { "name": "Pro",  "price_cents": 2900, "period": "month", "features": [{ "text": "10 sites" }, { "text": "SEO tools" }], "highlighted": true,  "cta_text": "Upgrade" }
                    ]
                }},
                { "type": "FAQ", "data": {
                    "heading": "Questions",
                    "items": [ { "question": "Can I cancel?", "answer": "Any time." } ]
                }},
                { "type": "Footer", "data": { "show_socials": true, "note": "" } }
            ],
            "seo": {
                "title": "Launch — ship faster",
                "meta_description": "The simplest way to get your product online."
            }
        }
    ]'::jsonb
),
(
    'portfolio-01',
    'Folio',
    'Minimal image-led portfolio. Client-rendered, no server needed.',
    'vite',
    ARRAY['Hero', 'Gallery', 'About', 'ContactForm', 'Footer'],
    2900,
    '{
        "colors":  { "primary": "#111111", "accent": "#F43F5E", "bg": "#FAFAFA", "text": "#111111" },
        "fonts":   { "heading": "Space Grotesk", "body": "Inter" },
        "radius":  "0px"
    }'::jsonb,
    '[
        {
            "slug": "",
            "title": "Work",
            "blocks": [
                { "type": "Hero", "data": {
                    "heading": "Selected work",
                    "subheading": "Photography and art direction.",
                    "image_url": "", "cta_text": "", "cta_link": "", "align": "left"
                }},
                { "type": "Gallery", "data": { "heading": "", "columns": 3, "images": [] } },
                { "type": "Footer",  "data": { "show_socials": true, "note": "" } }
            ],
            "seo": { "title": "Folio — selected work", "meta_description": "Photography and art direction portfolio." }
        }
    ]'::jsonb
)
ON CONFLICT (key) DO NOTHING;


-- ---------------------------------------------------------------------------
--  Verify it worked
-- ---------------------------------------------------------------------------
-- Run this and you should see 3 rows:
--    SELECT key, name, framework, array_length(block_types, 1) AS blocks
--    FROM templates ORDER BY name;
