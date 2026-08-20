-- Account profile: personal phone number + timezone, distinct from
-- Site.business's customer-facing contact info.
ALTER TABLE users ADD COLUMN IF NOT EXISTS phone TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS timezone TEXT;
