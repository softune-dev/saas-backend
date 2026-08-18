-- =============================================================================
--  reset_data.sql — WIPE ALL CUSTOMER DATA (development only)
-- =============================================================================
--
--  ⚠  THIS IS DESTRUCTIVE AND IRREVERSIBLE. There is no undo.
--     Never run this against a database holding real customer data.
--
--  THIS IS NOT A MIGRATION. It lives in scripts/, not migrations/, on purpose —
--  it must never be picked up as part of the numbered migration sequence.
--
--  WHAT IT DOES
--  Empties every table that holds tenant/customer data, while leaving the
--  schema itself (tables, columns, indexes, constraints, triggers) fully
--  intact. After running, every table below has zero rows and the database is
--  ready to be used again immediately — no migrations need re-running.
--
--  WHAT IT DELIBERATELY KEEPS
--  `templates` is NOT wiped. It is catalogue/seed data, not customer data, and
--  it is populated by migrations 004 / 005 / 007 / 008 (007 = Aurora, 008 =
--  Bazaar + a corrected Aurora seed). Wiping it would leave you unable to
--  create any site (POST /sites needs a template_id) until you re-ran those
--  seeds. If you genuinely want a bare database, uncomment the clearly marked
--  block at the bottom.
--
--  CASCADE IS BELT-AND-BRACES
--  Every table below is listed explicitly so the blast radius is readable at a
--  glance rather than inferred from foreign keys. CASCADE is still included in
--  case a future table gains an FK into one of these and is not added here.
--
--  RESTART IDENTITY resets any sequences so a fresh run starts from a clean
--  state rather than continuing old numbering.
-- =============================================================================

BEGIN;

TRUNCATE TABLE
    order_items,   -- order line snapshots
    orders,        -- customer orders
    inquiries,     -- contact-form submissions
    products,      -- catalogue items
    categories,    -- catalogue tree
    site_pages,    -- per-page block/SEO content
    sites,         -- storefronts
    users,         -- people who log into a tenant
    tenants        -- workspaces / accounts
RESTART IDENTITY CASCADE;

COMMIT;


-- =============================================================================
--  VERIFY — every count below must be 0
-- =============================================================================
SELECT 'tenants'    AS table_name, count(*) AS rows FROM tenants
UNION ALL SELECT 'users',       count(*) FROM users
UNION ALL SELECT 'sites',       count(*) FROM sites
UNION ALL SELECT 'site_pages',  count(*) FROM site_pages
UNION ALL SELECT 'categories',  count(*) FROM categories
UNION ALL SELECT 'products',    count(*) FROM products
UNION ALL SELECT 'orders',      count(*) FROM orders
UNION ALL SELECT 'order_items', count(*) FROM order_items
UNION ALL SELECT 'inquiries',   count(*) FROM inquiries
UNION ALL SELECT 'templates',   count(*) FROM templates  -- expected NON-zero
ORDER BY table_name;


-- =============================================================================
--  OPTIONAL — also wipe the template catalogue (rarely what you want)
-- =============================================================================
--  Only uncomment if you want a completely bare database. After running this
--  you MUST re-run migrations 004_seed.sql, 005_agency_template.sql and
--  007_aurora_template.sql before any site can be created again.
--
--  TRUNCATE TABLE templates RESTART IDENTITY CASCADE;
-- =============================================================================
