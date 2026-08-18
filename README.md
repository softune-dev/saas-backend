# SaaS Site Builder — Backend

Multi-tenant FastAPI backend. Customers buy a template, get an editable site, and
edit its content from an admin panel — with no redeploy per change.

| | |
|---|---|
| **API** | FastAPI (async) on your machine |
| **Database** | Supabase Postgres |
| **Cache** | Redis, in Docker |
| **Queue** | RabbitMQ, in Docker |
| **Interactive docs** | http://localhost:8000/docs once running |

New here? Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — it explains what
every file does and why the design is what it is.

---

## First-time setup

Do these once, in order. Each step has a verification you should actually run
before moving on.

### 1. Install Python packages

```bash
venv\Scripts\activate
```

```bash
pip install -r requirements.txt
```

Verify — should list fastapi, sqlalchemy, asyncpg, redis, aio-pika:

```bash
pip list
```

### 2. Create your .env

```bash
copy .env.example .env
```

Now open `.env` and fill in every line marked `<<< FILL`. The file tells you
step-by-step where to get each value. There are four:

- `SECRET_KEY` — generate it
- `SUPABASE_ANON_KEY` and `SUPABASE_SERVICE_ROLE_KEY` — from the dashboard
- `DATABASE_URL` — from the dashboard's "Connect" button
- `REVALIDATE_SECRET` — generate it

Generate the two secrets with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

### 3. Create the database tables

Open the [Supabase SQL Editor](https://supabase.com/dashboard) → your project →
**SQL Editor** → **New query**. Paste and run each file **in order**:

1. `migrations/001_core.sql` — extensions, tenants, users
2. `migrations/002_sites.sql` — templates, sites, pages
3. `migrations/003_commerce.sql` — categories, products, orders
4. `migrations/004_seed.sql` — three demo templates

Each should say *"Success. No rows returned"*. They are safe to re-run.

Verify — run this in the SQL Editor, expect 3 rows:

```sql
SELECT key, name, framework FROM templates ORDER BY name;
```

### 4. Start Redis and RabbitMQ

Install [Docker Desktop](https://www.docker.com/products/docker-desktop), start
it, wait for the whale icon to say *running*. Then:

```bash
docker compose up -d
```

Verify — both should show `healthy`:

```bash
docker compose ps
```

### 5. Start the server

```bash
uvicorn app.main:app --reload
```

Watch the startup lines. You want all three:

```
INFO  app | database: connected
INFO  app | redis: connected
INFO  app | rabbitmq: connected
```

Anything else → [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).

---

## Daily commands

Each block below is one command; run them in separate terminals where noted.

**Terminal 1 — the API** (always needed):

```bash
venv\Scripts\activate
```

```bash
uvicorn app.main:app --reload
```

**Terminal 2 — the background worker** (only when testing publish/queue jobs):

```bash
python -m app.worker
```

**Docker** (leave running; survives reboots unless you stop it):

```bash
docker compose up -d
```

```bash
docker compose down
```

**Tests:**

```bash
pytest
```

```bash
pytest tests/test_tenant_isolation.py -v
```

Stop the server or worker with `Ctrl+C` in its terminal.

---

## Where things are

```
app/
  main.py        entry point, middleware, /health
  config.py      reads .env — the only place env vars are touched
  db.py          engine + session (read its docstring re: the pooler)
  models.py      SQLAlchemy tables
  schemas.py     request/response validation
  security.py    passwords, JWT, current-user dependency
  crud.py        generic tenant-scoped helpers — the isolation boundary
  blocks.py      THE BLOCK REGISTRY — start here to understand the editor
  cache.py       Redis
  queue.py       RabbitMQ publisher
  worker.py      RabbitMQ consumer (separate process)
  api/
    auth.py      register / login / refresh / me
    sites.py     templates + sites + publish
    pages.py     page editing + GET /blocks
    commerce.py  categories, products, orders
    public.py    what the live customer sites call (no auth, cached)

migrations/      numbered SQL — the schema source of truth
tests/           pytest; test_tenant_isolation.py is the important one
docs/            architecture report + troubleshooting
postman/         importable Postman collection
```

---

## Endpoints

Full interactive reference at **http://localhost:8000/docs** — you can call every
endpoint from there without Postman.

| Method | Path | Auth | What |
|---|---|---|---|
| GET | `/health` | – | which dependencies are up |
| POST | `/auth/register` | – | create workspace + first user |
| POST | `/auth/login` | – | get tokens |
| POST | `/auth/refresh` | – | new tokens from a refresh token |
| GET | `/auth/me` | ✔ | current user + tenant |
| GET | `/templates` | – | catalogue |
| GET | `/blocks` | – | block registry (admin panel form spec) |
| GET/POST | `/sites` | ✔ | list / create sites |
| GET/PATCH/DELETE | `/sites/{id}` | ✔ | one site |
| POST | `/sites/{id}/publish` | ✔ | take live |
| POST | `/sites/{id}/unpublish` | ✔ | back to draft |
| GET/POST | `/sites/{id}/pages` | ✔ | list / create pages |
| GET/PATCH/DELETE | `/sites/{id}/pages/{pid}` | ✔ | one page — **the editor's Save** |
| GET/POST | `/sites/{id}/categories` | ✔ | categories |
| PATCH/DELETE | `/sites/{id}/categories/{cid}` | ✔ | one category |
| GET/POST | `/sites/{id}/products` | ✔ | products (`?q=` searches) |
| GET/PATCH/DELETE | `/sites/{id}/products/{pid}` | ✔ | one product |
| GET/POST | `/sites/{id}/orders` | ✔ | orders (`?status=` filters) |
| GET/PATCH | `/sites/{id}/orders/{oid}` | ✔ | one order |
| GET | `/public/site/{host}` | – | **full site config — what Vercel calls** |
| GET | `/public/site/{host}/sitemap.xml` | – | sitemap data |

✔ = send `Authorization: Bearer <access_token>`

---

## Your first walkthrough

Do this in Postman (import `postman/collection.json`) or at `/docs`:

1. `POST /auth/register` → copy `access_token` from the response
2. In Postman, set the collection variable `access_token`; at `/docs`, click
   **Authorize** and paste it
3. `GET /templates` → copy any `id`
4. `POST /sites` with that `template_id`, a `name`, and a `subdomain` like `mycafe`
5. `GET /sites/{id}/pages` → the template's default content is already there
6. `PATCH /sites/{id}/pages/{page_id}` → change a Hero `heading`, save
7. `POST /sites/{id}/publish`
8. `GET /public/site/mycafe` → the full config a live site would render, with
   resolved SEO and JSON-LD

Then call step 8 twice and compare the `X-Response-Time-ms` header — the second
one is served from Redis.

---

## Things worth trying, to learn how it behaves

- Break the tenant boundary on purpose: register a second account and try to
  `GET` the first account's site id. You should get 404. Then open
  [app/crud.py](app/crud.py), delete the `tenant_id` filter from `get_scoped`,
  and watch `pytest tests/test_tenant_isolation.py` go red. Put it back.
- Add a block type: add one entry to `REGISTRY` in [app/blocks.py](app/blocks.py),
  restart, and `GET /blocks`. No migration, no new endpoint.
- Stop Redis (`docker compose stop redis`) and call `/public/site/{host}`. It
  still works, just slower — cache failures are not request failures.
- Stop RabbitMQ and publish a site. Still works; the job is just skipped.
- Watch the queue: start the worker, open http://localhost:15672 (guest/guest),
  then publish a site.
- Set `DEBUG=true` and watch the SQL in your terminal as you call endpoints.
- In the Supabase SQL Editor, run `EXPLAIN ANALYZE` on a product search and look
  for `Bitmap Index Scan` on the trigram index.
