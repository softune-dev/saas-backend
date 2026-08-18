# Troubleshooting

**Start here every time: open http://localhost:8000/health.** It tells you which
of the three dependencies is unhappy, which usually identifies the problem before
you read any further.

```json
{ "database": "ok", "redis": "ok", "rabbitmq": "ok", "status": "ok" }
```

Also useful: errors are printed in the terminal running uvicorn. The browser or
Postman shows a short message; the terminal has the full story. Look there first.

---

## Startup and install

### `ModuleNotFoundError: No module named 'fastapi'`
The virtual environment isn't active, or packages aren't installed. Your prompt
should start with `(venv)`.

```bash
venv\Scripts\activate
```

```bash
pip install -r requirements.txt
```

### `ValidationError: secret_key: Field required` at startup
`.env` is missing or a required key is blank. Copy the example and fill the four
`<<< FILL` lines:

```bash
copy .env.example .env
```

### `ValidationError: secret_key: String should have at least 32 characters`
Your `SECRET_KEY` is too short. Generate a proper one:

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

### `ERROR: [Errno 10048] error while attempting to bind on address`
Port 8000 is already in use — usually an old uvicorn you forgot to stop.

Find it:

```bash
netstat -ano | findstr :8000
```

Kill it (use the PID from the last column):

```bash
taskkill /F /PID <pid>
```

Or just use another port:

```bash
uvicorn app.main:app --reload --port 8001
```

### The server starts but doesn't reload when I edit a file
You launched without `--reload`. Stop it with `Ctrl+C` and start again with the
flag. Note that `--reload` only watches `.py` files — a change to `.env` needs a
manual restart.

---

## Database

### `database: NOT connected` at startup

Work through these in order.

**1. Is `DATABASE_URL` using the async driver?**
It must start with `postgresql+asyncpg://`, not `postgresql://`. This is the most
common mistake. Symptom: `InvalidRequestError: The asyncio extension requires an
async driver`.

**2. Is the password right, and URL-encoded?**
If your database password contains `@ : / ? # %`, it breaks URL parsing. `@`
becomes `%40`, `#` becomes `%23`. Easiest fix: reset the password to letters,
digits, `-` and `_` only (Supabase dashboard → Project Settings → Database →
Reset database password).

**3. Did you paste the pooler URL, not the direct one?**
Use the **Transaction pooler** tab in the dashboard's "Connect" dialog. The
hostname should look like `aws-0-<region>.pooler.supabase.com` on port `6543`.

**4. Is the Supabase project paused?**
Free-tier projects pause after a week of inactivity. Open the dashboard — if it
says paused, click restore and wait a minute.

### `relation "tenants" does not exist`
The migrations haven't been run. Open the Supabase SQL Editor and run
`migrations/001_core.sql` through `004_seed.sql` in order. See README step 3.

### `type "citext" does not exist`
`001_core.sql` didn't run, or only partly ran. Run it again — it's idempotent.

### `InvalidSQLStatementNameError` / `DuplicatePreparedStatementError`, intermittently
This is the pooler + prepared statement clash. Check that `connect_args={"statement_cache_size": 0}`
is still present in [app/db.py](../app/db.py). Full explanation is in that file's
docstring. Do not remove that line while using the port-6543 URL.

### `server closed the connection unexpectedly`
The pooler reaped an idle connection. `pool_pre_ping=True` and `pool_recycle=300`
in `db.py` should prevent this — if you see it repeatedly, lower `pool_recycle`.

### `MissingGreenlet` or `greenlet_spawn has not been called`
You touched a lazily-loaded ORM attribute outside an active async session. Usually
means a relationship needs eager loading — add `lazy="selectin"` (for collections)
or `lazy="joined"` (for one-to-one) to the relationship in `models.py`.

### Tests fail with `assert row is not None, "No templates found"`
Run `migrations/004_seed.sql`.

---

## Docker, Redis, RabbitMQ

### `docker : The term 'docker' is not recognized`
Docker Desktop isn't installed, or isn't on PATH. Install it, then **restart your
terminal** — PATH changes don't apply to already-open windows.

### `error during connect: ... The system cannot find the file specified`
Docker Desktop is installed but not running. Start it and wait for the whale icon
to say *running*.

### `redis: unavailable` / `rabbitmq: unavailable`
Containers aren't up:

```bash
docker compose up -d
```

Check status — you want `healthy`, not `starting` or `exited`:

```bash
docker compose ps
```

RabbitMQ takes ~20 seconds to become healthy. Give it a moment before worrying.

### `Ports are not available: bind: address already in use`
Something else owns 6379 or 5672 — often a Redis or RabbitMQ you installed
natively earlier. Either stop that service, or change the host-side port in
`docker-compose.yml` (e.g. `"6380:6379"`) and update `REDIS_URL` in `.env` to
match.

### A container keeps restarting
Read its logs:

```bash
docker compose logs redis
```

```bash
docker compose logs rabbitmq
```

### Nuclear option — wipe all cached and queued data
Safe: nothing durable lives in Redis or RabbitMQ. Your Postgres data is untouched.

```bash
docker compose down -v
```

```bash
docker compose up -d
```

---

## Auth

### Every request returns 401 "Missing bearer token"
The header must be exactly `Authorization: Bearer <token>` — with the word
`Bearer`, a single space, and no quotes around the token. In Postman, use the
**Authorization** tab → type *Bearer Token* rather than typing the header by hand.

### 401 "Token expired"
Access tokens last 30 minutes. Call `POST /auth/refresh` with your refresh token,
or just log in again. To make development less annoying, raise
`ACCESS_TOKEN_EXPIRE_MINUTES` in `.env` — but never above 60 in production.

### 401 "Wrong token type"
You sent a refresh token where an access token belongs (or vice versa). The
`register`/`login` response contains both — `access_token` is the one for API
calls.

### 401 on every request right after changing `.env`
You changed `SECRET_KEY`, which invalidates every existing token. Log in again.

### 409 "An account with that email exists" but I can't log in
The account exists with a different password. Either use a different email, or
reset the row directly in the Supabase SQL Editor:

```sql
DELETE FROM tenants WHERE id = (SELECT tenant_id FROM users WHERE email = 'you@example.com');
```

(That deletes the whole workspace via cascade — fine in development.)

---

## Requests and validation

### 422 with a long `detail` array
Pydantic rejected the request body. The array tells you the exact field: `loc`
is the path to it, `msg` is what's wrong. Almost always a typo in a field name or
a wrong type (string where a number belongs).

### 422 "unknown block type 'X'"
That block type isn't in `REGISTRY` in [app/blocks.py](../app/blocks.py). Check
spelling — types are case-sensitive (`Hero`, not `hero`). `GET /blocks` lists the
valid ones.

### 422 "'Headline' is required"
A required field in a block's `data` is missing or empty. The message names the
field's label; `GET /blocks` shows which fields a type requires.

### 404 on something I know exists
Almost always tenant isolation doing its job — you're using a token from one
account and an id from another. Call `GET /auth/me` to confirm which tenant your
token belongs to.

It also happens on nested routes when the `site_id` in the path doesn't own the
child id: `/sites/{A}/pages/{page_of_site_B}` is a 404 by design.

### 409 "That subdomain is taken"
Subdomains are globally unique — including ones created by earlier test runs. Pick
another, or clean up:

```sql
SELECT subdomain, created_at FROM sites ORDER BY created_at DESC LIMIT 20;
```

### 500 with a generic "Internal server error"
Details are hidden unless `APP_ENV=development`. Set that in `.env`, or read the
full traceback in the uvicorn terminal — it's always printed there.

---

## Publishing and the queue

### I published a site but the live site still shows old content
Check in this order:

1. Is the **worker** running? It's a separate process:
   ```bash
   python -m app.worker
   ```
2. Is RabbitMQ up? `GET /health`.
3. Is `REVALIDATE_SECRET` set in `.env`? The worker skips the call with a warning
   if it's blank.
4. Is the template a Vite site? Those are skipped on purpose — they fetch config
   client-side and are never stale. The worker logs which.
5. Does the deployed site actually have an `/api/revalidate` route that accepts
   the `x-revalidate-secret` header? Until you build it, the worker logs a
   connection warning — expected.

### `GET /public/site/{host}` returns 404 for a site I just created
New sites are `draft`. Only published sites are public:

```
POST /sites/{id}/publish
```

### An edit doesn't show up in `/public/site/{host}`
Cache invalidation missed. Confirm by watching whether the change appears after
`CACHE_TTL_SECONDS`. To rule caching out entirely while debugging, set
`CACHE_TTL_SECONDS=0` in `.env` and restart.

If a specific write path is at fault, check it calls `cache.invalidate_site()` —
every mutating handler in `sites.py`, `pages.py` and `commerce.py` does.

### Jobs pile up in RabbitMQ and never drain
The worker isn't running, or it crashed. Look at http://localhost:15672
(guest/guest) → Queues → `saas_jobs`. "Ready" climbing with no consumer means
start the worker.

---

## Tests

### Tests hang or time out
Usually the database. Run `GET /health` first — the test suite needs Postgres, but
**not** a running uvicorn (it calls the app in-process).

### `fixture 'account' not found`
Run pytest from the project root, not from inside `tests/`.

### Tests leave rows behind after a crash
Each test deletes its own tenant, but a hard crash can skip cleanup. Test tenants
use `@example.test` emails, so:

```sql
DELETE FROM tenants WHERE id IN (SELECT tenant_id FROM users WHERE email LIKE '%@example.test');
```

### `test_tenant_isolation.py` fails
**Stop and fix this before doing anything else.** It means one tenant can reach
another's data. Check that the handler in question calls `crud.get_scoped` /
`crud.list_scoped` and never `select(Model).where(Model.id == ...)` directly.

---

## When you're properly stuck

1. `GET /health` — which dependency is down?
2. Read the uvicorn terminal — the full traceback is there.
3. Set `DEBUG=true` and watch the SQL. Often the query itself shows the problem.
4. Reproduce it at http://localhost:8000/docs — that removes Postman config as a
   variable.
5. Query the table directly in the Supabase SQL Editor. Is the data what you think
   it is?
6. Restart in order: worker, then uvicorn, then `docker compose restart`.

Nothing here is dangerous to break — it's your machine, there are no real users,
and the migrations are re-runnable. If the database gets into a confusing state,
you can drop everything and re-run the four migration files:

```sql
DROP TABLE IF EXISTS order_items, orders, products, categories, site_pages, sites, templates, users, tenants CASCADE;
```
