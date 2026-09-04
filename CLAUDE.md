# CLAUDE.md

Conventions for this repo. Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for
the reasoning behind them.

## Stack

FastAPI (async) · SQLAlchemy 2.0 + asyncpg · Supabase Postgres · Redis · RabbitMQ.
Python 3.11. API runs on the host; Redis and RabbitMQ run in Docker.

## Commands

```bash
venv\Scripts\activate
```

```bash
uvicorn app.main:app --reload
```

```bash
python -m app.worker
```

```bash
pytest
```

Do not install packages or start the server unless asked — the user runs those.

## Non-negotiable rules

**1. Tenant isolation goes through `app/crud.py`.**
Never write `select(Model).where(Model.id == x)` in a router for a tenant-owned
table. Use `crud.get_scoped` / `crud.list_scoped`, which require `tenant_id`.
Bypassing them is a data-leak bug, not a style preference.

**2. `tenant_id` on a new row comes from the verified parent, never from the
request body.** Resolve the site with `crud.get_scoped` first, then use
`site.tenant_id`.

**3. Cross-tenant access returns 404, never 403.** A 403 confirms the id exists.

**4. Any write to `site_pages.blocks` must go through
`blocks.validate_blocks()`.** JSONB has no database-level shape enforcement, so
the validator *is* the schema. An unvalidated write path lets malformed data reach
a customer's live site.

**5. Schema lives in `migrations/*.sql`, not `models.py`.** Add a new numbered
file; never edit an already-run one. Update `models.py` to match in the same
change, or the two drift.

**6. Every new foreign key needs an explicit index.** Postgres does not create
them. State in a comment which query the index serves.

**7. Money is integer cents.** Never float, never decimal strings.

**8. Order history is immutable.** Do not add total fields to `OrderUpdate`, and
never render a past order by joining to live `products` — that is what the
`*_snapshot` columns on `order_items` are for.

**9. Cache and queue failures must not fail a request.** Helpers in `cache.py`
and `queue.py` log and swallow. Keep it that way.

**10. `DATABASE_URL` must point at Supabase's session pooler** (port 5432 on
the `*.pooler.supabase.com` host), not the transaction pooler (port 6543) or
the direct connection (IPv6-only, unreachable from this host). The transaction
pooler forces prepared statements off, which measured ~700ms per trivial
query in production vs ~80ms on the session pooler — not a network latency
problem, a pooler-choice problem. See `app/db.py`'s docstring before changing
this.

## Conventions

- Separate `*Create` / `*Update` / `*Out` Pydantic models per resource. Update
  models have all-optional fields, applied with `exclude_unset=True`.
- List endpoints return a plain dict `{items, total, limit, offset}` and declare
  `response_model=Page[XOut]`. Do not return a `Page(...)` instance — Pydantic
  rejects the unparameterised class.
- Nested routes (`/sites/{site_id}/...`) call `_owned_site()` first.
- Mutating handlers call `cache.invalidate_site(...)`; publish-affecting ones also
  `queue.publish(...)`.
- Constraint violations become clean 409s via `crud.save`; add new constraint
  names to `crud._explain`.
- Comments explain *why*, not *what*. Match the existing density — this codebase
  is a teaching artifact and comments carry the reasoning.
- Keep files under 500 lines.

## Testing

`pytest` runs against the real Supabase database. Each test creates its own tenant
via the `account` / `two_accounts` fixtures and deletes it afterwards.

Adding a tenant-owned resource? Add isolation tests for it in
`tests/test_tenant_isolation.py`. That file is the security boundary — never
weaken or skip a test in it to get a green run.

## Skills

- `/add-block-type` — add an editable section to the block registry
- `/add-migration` — schema change with index discipline
- `/add-resource` — new tenant-scoped CRUD resource
- `/add-template-skin` — scaffold a new storefront visual design matching the
  dashboard's fixed section/page contract
