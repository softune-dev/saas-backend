-- Site Settings: shipping locations, FAQs, and legal pages (Privacy/Terms).
-- business/seo already exist on sites (see migration 001) — these three are
-- new concerns with no existing JSONB column to reuse.

ALTER TABLE sites
    ADD COLUMN IF NOT EXISTS shipping jsonb NOT NULL DEFAULT '{"locations": []}'::jsonb,
    ADD COLUMN IF NOT EXISTS faqs jsonb NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS legal jsonb NOT NULL DEFAULT '{}'::jsonb;
