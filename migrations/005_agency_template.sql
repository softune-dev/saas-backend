-- =============================================================================
--  005_agency_template.sql — a fourth template: agency/services landing page
-- =============================================================================
--  Run AFTER 004_seed.sql. Safe to re-run (ON CONFLICT DO NOTHING).
--
--  Backs the softune-derived template. Uses ONLY block types that already exist
--  in app/blocks.py (Hero, About, Testimonials, ContactForm, Footer) — no
--  registry change needed. Softune's own bespoke sections (Services grid,
--  CaseStudy) are intentionally dropped: they have no block equivalent and are
--  specific to a dev agency's own marketing copy, not something a generic
--  customer's site needs.
-- =============================================================================

INSERT INTO templates (key, name, description, framework, block_types, price_cents, default_theme, default_pages)
VALUES
(
    'agency-01',
    'Studio',
    'Dark, motion-forward agency/services landing page.',
    'nextjs',
    ARRAY['Hero', 'About', 'Testimonials', 'ContactForm', 'Footer'],
    6900,
    '{
        "colors":  { "primary": "#5B8DEF", "accent": "#5B8DEF", "bg": "#0B0B0C", "text": "#F5F5F5" },
        "fonts":   { "heading": "Inter", "body": "Inter" },
        "radius":  "16px"
    }'::jsonb,
    '[
        {
            "slug": "",
            "title": "Home",
            "blocks": [
                { "type": "Hero", "data": {
                    "heading": "Empowering startups to launch, scale, and succeed faster",
                    "subheading": "Creative and development studio.",
                    "image_url": "",
                    "cta_text": "Let's talk",
                    "cta_link": "#contact",
                    "align": "left"
                }},
                { "type": "About", "data": {
                    "heading": "About us",
                    "body": "A modern full-service studio empowering brands through design, development, and strategy.",
                    "image_url": ""
                }},
                { "type": "Testimonials", "data": {
                    "heading": "What clients say",
                    "items": [
                        { "quote": "They shipped faster than any agency we have worked with.",
                          "author": "Jordan Lee", "role": "Founder, Nimbus" }
                    ]
                }},
                { "type": "ContactForm", "data": {
                    "heading": "Start a project",
                    "fields": [
                        { "name": "name",    "required": true  },
                        { "name": "email",   "required": true  },
                        { "name": "message", "required": true  }
                    ],
                    "submit_text": "Send",
                    "success_message": "Thanks — we will be in touch shortly."
                }},
                { "type": "Footer", "data": { "show_socials": true, "note": "" } }
            ],
            "seo": {
                "title": "Studio — a creative and development studio",
                "meta_description": "Design, development and strategy for startups."
            }
        }
    ]'::jsonb
)
ON CONFLICT (key) DO NOTHING;
