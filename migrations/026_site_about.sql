-- Dedicated About Us content — separate from theme.tagline (short strapline)
-- and business.description (one-line SEO/contact blurb). Shape:
--   { heading: str, image: str, paragraphs: str[] }
ALTER TABLE sites ADD COLUMN IF NOT EXISTS about JSONB NOT NULL DEFAULT '{}'::jsonb;
