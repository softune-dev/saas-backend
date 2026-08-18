---
name: add-migration
description: Add or change a database table, column, constraint, or index in this project. Use when the user wants a schema change, a new table, a new index, or mentions migrations, DDL, or Supabase SQL.
---

# Add a migration

`migrations/*.sql` is the schema source of truth — not `app/models.py`. Expression
indexes, partial indexes, `INCLUDE` columns and triggers have no clean ORM
equivalent, and those are where the performance lives.

## Steps

### 1. Create the next numbered file

Check what exists, then create `migrations/00N_short_name.sql`.
**Never edit an already-run file** — the user has applied it to their live Supabase
project. Additive-only.

### 2. Make it idempotent

The user may re-run it. Use `IF NOT EXISTS`, `OR REPLACE`, `ON CONFLICT DO
NOTHING`, and `DROP TRIGGER IF EXISTS` before `CREATE TRIGGER`.

### 3. Follow the established patterns

```sql
CREATE TABLE IF NOT EXISTS thing (
    id         uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id  uuid        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    site_id    uuid        NOT NULL REFERENCES sites(id)   ON DELETE CASCADE,
    name       text        NOT NULL,
    slug       citext      NOT NULL,
    status     text        NOT NULL DEFAULT 'draft'
                           CHECK (status IN ('draft', 'active')),
    config     jsonb       NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_thing_site_slug UNIQUE (site_id, slug)
);

DROP TRIGGER IF EXISTS trg_thing_updated_at ON thing;
CREATE TRIGGER trg_thing_updated_at
    BEFORE UPDATE ON thing
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
```

Required choices:
- **`tenant_id` on every tenant-owned table**, denormalised even when derivable.
  It makes isolation a single-table filter with no JOIN.
- **`citext`** for anything case-insensitive (emails, slugs, hostnames).
- **`CHECK`, not `ENUM`** — changing a CHECK is one line; `ALTER TYPE` is painful.
- **integer cents** for money, never float.
- **`updated_at` trigger** on any table with that column.
- Cascade direction is a real decision: `CASCADE` when the child is meaningless
  alone, `RESTRICT` to protect shared records, `SET NULL` when the child must
  survive (plus snapshot columns if it holds history).

### 4. Index every foreign key, and comment why

Postgres does **not** index FKs automatically. Missing FK indexes are the most
common performance bug in this kind of schema.

```sql
-- INDEX: the admin list — one site, newest first.
-- Equality column first, sort column second with DESC baked in, so Postgres
-- walks the index in output order and skips the sort.
CREATE INDEX IF NOT EXISTS idx_thing_site_created
    ON thing (site_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_thing_tenant ON thing (tenant_id);
```

Reach for these when they fit:
- **Partial** (`WHERE is_active`) when the app always filters that way — smaller
  index, more of it stays in RAM.
- **`INCLUDE (...)`** on hot list paths — enables index-only scans.
- **GIN + `gin_trgm_ops`** for `ILIKE '%x%'` search. A b-tree cannot serve a
  leading wildcard.
- **GIN + `jsonb_path_ops`** for JSONB containment queries.
- **Expression index** (`((data ->> 'email'))`) to make a nested JSON field as
  fast as a real column.

Every index gets a comment naming the query it serves. An index with no known
query is write-amplification for nothing.

### 5. Mirror it in `app/models.py`

Add the matching SQLAlchemy model or columns. Use the `_pk()`, `_created()`,
`_updated()` and `TimestampMixin` helpers already there. Nothing enforces that
the two agree — that is why it must be the same change.

### 6. Do NOT run it

Tell the user to run it themselves:

> Open the Supabase dashboard → SQL Editor → New query, paste
> `migrations/00N_name.sql`, and Run. Expect "Success. No rows returned".

## Verify afterwards

Give the user a check query, and for index work an `EXPLAIN ANALYZE` so they can
confirm the planner actually uses it:

```sql
EXPLAIN ANALYZE SELECT * FROM thing WHERE site_id = '...' ORDER BY created_at DESC LIMIT 20;
```

Look for `Index Scan` / `Index Only Scan`, not `Seq Scan`.
