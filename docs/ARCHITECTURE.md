# Architecture Report

Read this once, top to bottom. It explains what every file does, why each design
decision was made, and what the known weak points are.

---

## 1. What this system does

A customer signs up, buys a template, and gets a website they can edit from an
admin panel. The website itself is hosted on Vercel (Next.js or Vite). This
backend holds all the *content* and serves it to those sites over HTTP.

The important consequence: **editing content never triggers a redeploy.** The
template code on Vercel is static; the content it renders comes from
`GET /public/site/{host}` at request time. A save writes to Postgres, clears a
Redis key, and (for Next.js) queues a cache-refresh call. The visitor sees the
change in seconds.

```
  Admin panel  ──PATCH /sites/x/pages/y──▶  this API  ──▶ Postgres (Supabase)
                                               │
                                               ├──▶ Redis: drop cached config
                                               └──▶ RabbitMQ: "revalidate site"
                                                          │
                                                     worker.py
                                                          │
                                          POST https://acme.vercel.app/api/revalidate
                                                          │
  Visitor ──▶ acme.vercel.app ──GET /public/site/acme──▶ this API ──▶ Redis hit
```

---

## 2. The three-layer content model

This is the core idea. Everything else follows from it.

**Layer 1 — the block registry** ([app/blocks.py](../app/blocks.py))
A catalogue of every section type that can exist (`Hero`, `Pricing`, `FAQ`, …),
each described as *data*: a list of fields with types, labels and limits. Lives in
code, not the database, because it is a contract shared with the template repos.

**Layer 2 — the template manifest** (`templates.block_types`)
Which block types a given template knows how to render, in default order.
Template A can use `[Hero, MenuList, ContactForm]` and Template B
`[Hero, Pricing, FAQ]` with no overlap.

**Layer 3 — the site's content** (`site_pages.blocks`, a JSONB array)
The actual values, as an ordered array of `{type, data}`. Order in the array is
render order on the page.

### What this buys you

- **One admin panel edits every template.** It calls `GET /blocks` once and
  generates its edit forms from the response. It does not know what a "Hero" is.
- **No migration when sites differ.** A site that needs a Pricing block and one
  that doesn't are just different JSON arrays in the same column. This was the
  central question you asked, and JSONB is the answer.
- **Adding a block type is one dict entry.** No migration, no endpoint, no model,
  no admin-panel deploy.

### The cost, stated honestly

JSONB gives up the database's ability to validate that content. So validation
moved into `blocks.validate_blocks()`, which runs on every page write. Nothing
reaches the JSONB column unvalidated — the API is the schema. That means **an
unvalidated write path is a real bug**, not a style issue: if you add a route that
writes `blocks` without calling the validator, malformed data gets in and some
template crashes at render time on a customer's live site.

The other cost: customers can only use blocks their template defines. They cannot
build arbitrary layouts. That is the right trade for a productised SaaS — you keep
design quality under control — but it is a limit, and a true visual editor is a
much bigger project if you ever decide you need one.

---

## 3. Multi-tenancy — where the security actually lives

Every tenant-owned table carries a `tenant_id`. Isolation is enforced in
**[app/crud.py](../app/crud.py)**, where every read and write helper *requires*
`tenant_id` as an argument and puts it in the `WHERE` clause.

There is deliberately no "get by id" that skips it. You cannot forget the filter,
because the function will not run without it.

Two details that matter:

**`tenant_id` is denormalised** onto `site_pages`, `products`, `categories`,
`orders` — derivable through the parent, but stored anyway. Ownership checks and
tenant-scoped lists become single-table index scans with no JOIN. Faster, and no
chance of writing the join wrong and leaking.

**Cross-tenant access returns 404, never 403.** A 403 would confirm the id is
real, letting someone enumerate other tenants' record ids. "Not found" leaks
nothing.

### Why Row Level Security is NOT used

Supabase pushes RLS because its usual pattern is a browser talking directly to
Postgres. Ours is different: this backend connects as the database owner, and the
owner **bypasses RLS**. Enabling it would filter nothing while looking like
security — the worst possible outcome.

**[tests/test_tenant_isolation.py](../tests/test_tenant_isolation.py) is the real
boundary.** It has tenant B attempt to read, edit and delete tenant A's sites,
pages, products and orders. Treat a failure there as a production incident.

If you later let browsers hit Supabase directly (client-side image uploads, say),
turn RLS on for those specific tables at that point.

---

## 4. Database design

The SQL in `migrations/` is the source of truth, not `models.py`. Expression
indexes, partial indexes, `INCLUDE` columns and triggers have no clean ORM
equivalent, and those are exactly where the performance lives. Every index in
those files has a comment explaining the query it serves — read them.

The principles applied:

| Decision | Why |
|---|---|
| **Every FK is explicitly indexed** | Postgres does *not* index foreign keys automatically. This is the single most common performance mistake. Without it, a child lookup — and every parent `DELETE` — is a sequential scan. |
| **Composite index column order: equality, then sort** | `(site_id, created_at DESC)` lets Postgres seek to one site and then walk the index already in output order, skipping the sort entirely. Reversed, the index is nearly useless. |
| **Partial indexes** (`WHERE is_active`, `WHERE status='published'`) | The app almost always filters on these. A smaller index keeps more of itself in RAM and touches fewer pages. |
| **`INCLUDE` columns** on hot list indexes | Enables index-only scans: listing 24 products reads one index instead of 24 random heap fetches. |
| **GIN + trigram on `products.name`** | A b-tree cannot serve `ILIKE '%foo%'`. Without trigrams, every product search is a full table scan. |
| **Expression index on `(customer ->> 'email')`** | Makes a lookup on a nested JSON field as fast as one on a real column. This is what keeps JSONB from becoming a performance trap. |
| **`citext` for emails, slugs, domains** | `Bob@x.com` and `bob@x.com` must be one row. Doing it in Python means remembering `.lower()` at every call site; one miss is a duplicate account. |
| **Money as integer cents** | `0.1 + 0.2 != 0.3` in floating point, and those fractions become real accounting errors. Also maps cleanly to JSON and to payment APIs. |
| **`CHECK` constraints instead of Postgres `ENUM`** | Same safety. Adding a value to a real ENUM needs `ALTER TYPE`; changing a CHECK is one line. |
| **`updated_at` maintained by trigger** | Cannot be forgotten. A manual fix in the Supabase dashboard gets a correct timestamp too. |

### Cascade choices are deliberate — read this before changing one

- `tenants → users, sites, pages, products, orders`: **CASCADE**. Deleting an
  account removes its data. One statement, no orphans.
- `templates → sites`: **RESTRICT**. You must not delete a template live customer
  sites depend on. Retire it with `is_active = false` instead.
- `categories → products`: **SET NULL**. Deleting a category orphans its products
  up to uncategorised. It must never delete the customer's products.
- `products → order_items`: **SET NULL**, plus snapshot columns. See below.

### Orders are immutable history

`order_items` stores `name_snapshot`, `sku_snapshot` and `unit_price_cents` — what
the buyer actually saw and agreed to pay. Order totals are stored, not computed
on read.

This is not redundancy. If you rendered an old order by joining to the live
`products` table, then a price change next week would silently rewrite last
month's invoice. **Never render a historical order from current product data.**

---

## 5. Performance

Three layers, cheapest first:

1. **Redis** ([app/cache.py](../app/cache.py)) fronts `/public/site/{host}` — the
   highest-traffic path by orders of magnitude. Every visitor to every customer
   site hits it, and the answer only changes when the owner saves. Invalidation is
   write-through (a save *deletes* the key), so edits appear immediately; the TTL
   is only a safety net.
2. **Indexes** make the cold path fast, which matters because a cache miss happens
   on every edit and every deploy.
3. **Query shape**: `Order.items` uses `lazy="selectin"` (2 queries for 50 orders,
   not 51); order creation fetches all products in one `IN` query, not a loop.

`X-Response-Time-ms` is on every response. Call a public endpoint twice and watch
it drop from ~200ms to ~2ms.

### Auth skips a database round-trip

`get_principal` reads identity from the JWT's claims — no `SELECT` on `users`.
That saves a round-trip on *every authenticated request*. The cost: deactivating
a user doesn't take effect until their access token expires (30 min default). If
you need instant revocation later, add a Redis deny-list — still no DB hit.

### The pooler gotcha — read this if connections act strange

`DATABASE_URL` uses Supabase's transaction pooler (port 6543), which hands each
query whatever backend connection is free. asyncpg's default prepared-statement
caching assumes a stable connection, so you get intermittent
`InvalidSQLStatementNameError` that vanishes on retry.

`statement_cache_size: 0` in [app/db.py](../app/db.py) is the fix. **Do not remove
it** while using the 6543 URL. The full explanation is in that file's docstring.

---

## 6. Cache and queue both degrade gracefully

Redis and RabbitMQ failures are logged and swallowed:

- Cache down → every request is a miss. Slower, not broken.
- Queue down → the job is skipped. The content is *already saved* in Postgres; a
  cache-refresh call is not worth failing a user's save over.

Startup warns about both but does not exit, so you can work on API and database
code with Docker stopped. Postgres is the only hard dependency.

**Corollary: anything that must never be lost belongs in Postgres, not only in the
queue.** Today nothing does. If you add "charge the customer" as a job, that rule
becomes load-bearing.

---

## 7. SEO

Resolved server-side in [app/api/public.py](../app/api/public.py), not in the
templates. One implementation, so every template — Next.js or Vite, now or in two
years — gets identical correct metadata.

- **Per-page over site-wide fallback**: page SEO wins, site SEO fills gaps. A
  customer sets an OG image once, not on all nine pages.
- **JSON-LD generated from `sites.business`**: the customer types their address
  and phone during setup and gets rich-result-eligible structured data for free.
  Never ask for it twice.
- **Canonical URL per page**: necessary because a site is reachable at both
  `acme.vercel.app` and `acme.com` once a custom domain is attached. Without it
  Google sees two copies and splits the ranking.
- **`noindex` while draft**: set on creation, cleared by `/publish`. A
  half-finished page ranking under the customer's name is worse than no page.
- **Sitemap** generated from the page list, served as JSON so each template
  renders `/sitemap.xml` on its *own* domain.

### Next.js vs Vite — a real difference

Next.js renders server-side, so search engines get real `<title>` and `<meta>` in
the initial HTML. A plain Vite SPA only has them after JavaScript runs, which
engines handle inconsistently. **If a customer's site needs to rank, prefer
Next.js templates**, or add a prerender step to the Vite ones. The `framework`
column exists to let you treat them differently — the worker already skips
revalidation for Vite, since those sites are never stale.

---

## 8. Known weak points

Honest list. None block you today; all will matter eventually.

1. **`next_order_number` races.** It counts existing rows, so two simultaneous
   checkouts can compute the same number. The UNIQUE constraint catches it (the
   second gets a 409), so no duplicate is ever stored — but a customer sees a
   failed checkout. Proper fix: a per-site Postgres sequence. Good exercise.
2. **Stock decrement is not locked.** Two concurrent orders for the last item can
   both pass the check. Fix: `SELECT ... FOR UPDATE` on the product rows, or a
   `CHECK (stock >= 0)` plus retry.
3. **No rate limiting.** `/auth/login` can be brute-forced. Add slowapi or do it
   at the edge before going live.
4. **No refresh-token rotation or revocation.** A leaked refresh token is valid
   for 14 days. Fix: store token ids and invalidate on use.
5. **Failed jobs are dropped, not retried.** `requeue=False` in the worker is
   deliberate — a message failing from a *bug* would fail forever and block the
   queue. Real systems use a dead-letter queue.
6. **No schema migration tool.** Numbered SQL files run by hand, which is fine
   now and won't be with a team. `DIRECT_URL` is already in `.env` for when you
   add Alembic.
7. **`models.py` can drift from `migrations/*.sql`.** Nothing enforces they agree.
   Change both together.
8. **Tests run against the real Supabase database.** Deliberate — a mock wouldn't
   exercise the constraints and indexes that are the actual engineering. Each test
   makes its own tenant and deletes it after. But do not point `DATABASE_URL` at
   a database with real customer data and run `pytest`.

---

## 9. File map

| File | Responsibility |
|---|---|
| `app/main.py` | entry point, CORS, timing middleware, `/health`, lifespan checks |
| `app/config.py` | the only place `.env` is read; fails loudly at startup |
| `app/db.py` | async engine, session factory, the pooler fix |
| `app/models.py` | SQLAlchemy tables (mirrors the SQL; SQL is authoritative) |
| `app/schemas.py` | Pydantic in/out models; separate Create/Update/Read per resource |
| `app/security.py` | bcrypt, JWT encode/decode, `get_principal`, `require_admin` |
| `app/crud.py` | **generic tenant-scoped helpers — the isolation boundary** |
| `app/blocks.py` | **the block registry — the editor's contract** |
| `app/cache.py` | Redis; graceful degradation; `invalidate_site` |
| `app/queue.py` | RabbitMQ publisher; job type constants |
| `app/worker.py` | consumer process; revalidation, sitemap, email handlers |
| `app/api/auth.py` | register, login, refresh, me |
| `app/api/sites.py` | templates, sites, publish/unpublish |
| `app/api/pages.py` | page CRUD, `GET /blocks` |
| `app/api/commerce.py` | categories, products, orders |
| `app/api/public.py` | unauthenticated cached site config, SEO resolution, JSON-LD |
| `migrations/001_core.sql` | extensions, `set_updated_at()`, tenants, users |
| `migrations/002_sites.sql` | templates, sites, site_pages |
| `migrations/003_commerce.sql` | categories, products, orders, order_items |
| `migrations/004_seed.sql` | three demo templates (also documents block format) |
| `tests/conftest.py` | fixtures; `two_accounts` powers the isolation tests |
| `tests/test_tenant_isolation.py` | **the security boundary** |
| `tests/test_registry_consistency.py` | guards registry ↔ seed ↔ manifest drift |

---

## 10. What to build next

In dependency order:

1. **The admin panel frontend.** Consume `GET /blocks`, render forms from the
   specs, `PATCH` pages. Add an iframe live preview beside the form — `postMessage`
   for instant updates, refresh-on-save if you want it simpler.
2. **Image uploads.** Create the `site-assets` bucket in Supabase Storage, upload
   from the browser with the anon key, store the returned URL in the block's
   `image_url`. Never put image bytes in Postgres.
3. **One real template on Vercel.** Fetch `/public/site/{host}` in
   `generateMetadata` and the page component; add `/api/revalidate` guarded by
   `REVALIDATE_SECRET`. This proves the whole no-redeploy loop end to end.
4. **Funnels.** A funnel is an ordered list of pages plus step tracking. The block
   registry already handles the page content; you need a `funnels` table and a
   `funnel_steps` join, and `orders.meta` is already there to record attribution.
5. **Billing.** Add a `subscriptions` table; `tenants.plan` and `tenants.settings`
   already exist to hold limits.
6. **Custom domains.** `sites.custom_domain` and the canonical logic are already
   in place; you need the Vercel domains API and a verification flow.

Before going live, fix weak points 2 and 3 from section 8 (stock locking and rate
limiting) at minimum.
