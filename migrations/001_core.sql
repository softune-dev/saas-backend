-- =============================================================================
--  001_core.sql — extensions, shared helpers, tenants, users
-- =============================================================================
--  HOW TO RUN
--    1. Supabase dashboard -> your project -> "SQL Editor" (left sidebar).
--    2. Click "New query".
--    3. Paste this ENTIRE file, click "Run" (or Ctrl+Enter).
--    4. Expect "Success. No rows returned".
--    5. Then run 002, 003, 004 in order.
--
--  Every statement is idempotent (IF NOT EXISTS / OR REPLACE), so re-running
--  this file is safe and will not destroy data.
-- =============================================================================


-- ---------------------------------------------------------------------------
--  EXTENSIONS
-- ---------------------------------------------------------------------------
-- citext = case-insensitive text. Used for emails, slugs, subdomains, domains.
-- WHY: without it, 'Bob@x.com' and 'bob@x.com' are two different rows, and a
-- UNIQUE constraint won't stop a duplicate signup. Solving it in application
-- code means remembering to .lower() at every single call site — one miss and
-- you have a duplicate account. The database should enforce this, not you.
CREATE EXTENSION IF NOT EXISTS citext;

-- pg_trgm = trigram matching. Powers fast ILIKE '%foo%' searches via GIN index.
-- WHY: a plain b-tree index cannot help a leading-wildcard search; without this
-- every "search products" query is a full table scan.
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- gen_random_uuid() is built into Postgres 13+, so no extension needed for UUIDs.


-- ---------------------------------------------------------------------------
--  SHARED HELPER: auto-maintain updated_at
-- ---------------------------------------------------------------------------
-- One function, reused by a trigger on every table that has updated_at.
-- WHY a trigger instead of setting it in Python: it cannot be forgotten. Any
-- write — from the API, from a manual SQL fix in the dashboard, from a future
-- worker script — gets a correct timestamp. Application-level timestamps drift
-- the moment someone edits a row outside the app.
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;


-- =============================================================================
--  TENANTS — one row per paying customer account (an org, not a person)
-- =============================================================================
-- This is the root of the ownership tree. Everything else in the system hangs
-- off a tenant_id, and that column is what keeps customer A from ever seeing
-- customer B's data.
CREATE TABLE IF NOT EXISTS tenants (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Short handle, used in admin URLs and as the default site subdomain.
    slug        citext      NOT NULL UNIQUE,

    name        text        NOT NULL,

    -- CHECK constraint instead of a Postgres ENUM type. Deliberate: adding a
    -- value to a real ENUM requires ALTER TYPE and cannot run inside some
    -- transactions; changing a CHECK is a one-line ALTER. Same safety, far less
    -- migration pain when you invent a new plan tier next month.
    plan        text        NOT NULL DEFAULT 'free'
                            CHECK (plan IN ('free', 'starter', 'pro', 'enterprise')),

    status      text        NOT NULL DEFAULT 'active'
                            CHECK (status IN ('active', 'suspended', 'cancelled')),

    -- Flexible bucket for per-tenant limits/flags (max_sites, feature toggles).
    -- Keeps you from running a migration every time you add a plan knob.
    settings    jsonb       NOT NULL DEFAULT '{}'::jsonb,

    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- The UNIQUE on slug already creates a b-tree index, so lookup-by-slug is
-- covered. No extra index needed — a redundant index costs write throughput and
-- disk for zero read benefit.

DROP TRIGGER IF EXISTS trg_tenants_updated_at ON tenants;
CREATE TRIGGER trg_tenants_updated_at
    BEFORE UPDATE ON tenants
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();


-- =============================================================================
--  USERS — people who log into the admin panel
-- =============================================================================
CREATE TABLE IF NOT EXISTS users (
    id            uuid        PRIMARY KEY DEFAULT gen_random_uuid(),

    -- ON DELETE CASCADE: deleting a tenant removes its users. Correct here —
    -- a user has no meaning without their org. Contrast with products below,
    -- where we deliberately do NOT cascade.
    tenant_id     uuid        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,

    -- Globally unique, not unique-per-tenant. TRADEOFF, chosen on purpose:
    --   * Globally unique  -> login is "find user by email", one index hit, no
    --     "which tenant are you?" step in the login form. Much simpler.
    --   * Unique per tenant -> the same human can belong to two orgs, but now
    --     login needs a tenant hint (subdomain, or a picker UI).
    -- Start global. If you ever need multi-org membership, that is a new
    -- `memberships` join table, not a change to this column.
    email         citext      NOT NULL UNIQUE,

    -- bcrypt output. Never the password itself. 60 chars today, but leave it
    -- as text so a future switch to argon2 (longer hashes) needs no migration.
    password_hash text        NOT NULL,

    full_name     text,

    role          text        NOT NULL DEFAULT 'owner'
                              CHECK (role IN ('owner', 'admin', 'member')),

    is_active     boolean     NOT NULL DEFAULT true,

    last_login_at timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

-- INDEX: users by tenant.
-- WHY THIS EXISTS: Postgres does NOT automatically index foreign key columns.
-- This is the single most common performance mistake people make. Without it,
-- "list the team members of this tenant" is a sequential scan of every user in
-- your entire system, and — worse — every DELETE on tenants has to scan users
-- to enforce the cascade.
CREATE INDEX IF NOT EXISTS idx_users_tenant ON users (tenant_id);

-- INDEX: active users only (partial index).
-- WHY PARTIAL: the app almost always filters `is_active = true`. Indexing only
-- those rows makes the index physically smaller than a full one, so more of it
-- stays in RAM and lookups touch fewer pages. Deactivated users are rare and
-- rarely queried, so they don't deserve index space.
CREATE INDEX IF NOT EXISTS idx_users_tenant_active
    ON users (tenant_id)
    WHERE is_active;

DROP TRIGGER IF EXISTS trg_users_updated_at ON users;
CREATE TRIGGER trg_users_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();


-- =============================================================================
--  ROW LEVEL SECURITY — read this, it explains a deliberate choice
-- =============================================================================
-- Supabase leans hard on RLS because its normal pattern is a browser talking
-- straight to Postgres with the anon key. OUR pattern is different: this FastAPI
-- backend connects as the database owner over the pooler, and the database owner
-- BYPASSES RLS. So enabling RLS here would give you a false sense of safety —
-- it would not filter a single one of our queries.
--
-- Where tenant isolation is ACTUALLY enforced in this project:
--   app/crud.py  — every read/write helper takes tenant_id and adds it to the
--                  WHERE clause. There is no code path that queries a
--                  tenant-owned table without it.
--   tests/test_tenant_isolation.py — proves it, by having tenant B attempt to
--                  read and mutate tenant A's rows and asserting 404s.
--
-- That test file is the real security boundary. Treat a failure there as a
-- production incident, not a broken test.
--
-- IF you later let browsers hit Supabase directly (e.g. client-side image
-- uploads to Storage), turn RLS on for the tables involved at that point.
-- =============================================================================
