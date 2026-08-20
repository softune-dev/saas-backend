-- Account profile picture — real Cloudinary URL or a generated data: URI
-- for a preset avatar. Plain TEXT either way.
ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar_url TEXT;
