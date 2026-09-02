-- =============================================================================
--  050_demo_access_requests.sql — capture an email before granting demo access
-- =============================================================================
--  The public "See a live demo" button used to hand out real tokens for
--  free, no email, nothing recorded — no way to reach out to anyone who
--  tried it. One row per email (not one row per click): a repeat visit
--  updates last_requested_at/request_count instead of piling up duplicate
--  rows, so this stays a clean outreach list, not a click log.
-- =============================================================================

CREATE TABLE IF NOT EXISTS demo_access_requests (
    id                 uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    email              citext      NOT NULL UNIQUE,
    ip                 text,
    request_count      int         NOT NULL DEFAULT 1,
    first_requested_at timestamptz NOT NULL DEFAULT now(),
    last_requested_at  timestamptz NOT NULL DEFAULT now()
);

-- Superadmin's list sorts by recency — see app/api/superadmin.py.
CREATE INDEX IF NOT EXISTS idx_demo_access_requests_last_requested_at
    ON demo_access_requests (last_requested_at DESC);

ALTER TABLE demo_access_requests ENABLE ROW LEVEL SECURITY;
