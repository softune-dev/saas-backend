"""Async database engine and session factory.

READ THIS IF CONNECTIONS MISBEHAVE — the pooler gotcha
------------------------------------------------------
Supabase's transaction pooler (Supavisor, port 6543) hands your query whatever
backend connection is free at that moment. It does not guarantee the same one
twice.

asyncpg by default optimises by PREPARING each statement on the server and
reusing it by name. Those prepared statements live on ONE specific backend
connection. So you get:

    request 1 -> backend A -> "PREPARE __asyncpg_stmt_1__"
    request 2 -> backend B -> "EXECUTE __asyncpg_stmt_1__"  -> ERROR: does not exist
    request 3 -> backend A again -> "PREPARE __asyncpg_stmt_1__" -> ERROR: already exists

The symptom is `InvalidSQLStatementNameError` or `DuplicatePreparedStatementError`
— genuinely nasty to diagnose if you don't know to look for it.

THREE connect_args are needed together, not one — confirmed empirically (20
concurrent connections, zero errors, after adding all three; still failing on
literally the first query with any one of them missing):

  - "statement_cache_size": 0
    asyncpg's OWN native cache. Without this, whatever internal/passthrough
    path the dialect uses for its initial connection probe (a plain
    `select pg_catalog.version()` run before any of our code executes) still
    uses asyncpg's default sequential naming and collides immediately.

  - "prepared_statement_cache_size": 0
    SQLAlchemy's asyncpg dialect wraps every connection in
    `AsyncAdapt_asyncpg_connection`, which does its OWN SEPARATE prepared-
    statement bookkeeping for anything run through the normal DBAPI cursor
    (i.e. everything the ORM/Core does). This is the one most blog posts
    mean when they say "statement_cache_size" — but that's not its name here,
    and asyncpg's native option above does not cover this path.

  - "prepared_statement_name_func"
    Even with both caches off, the wrapper's default namer still hands out
    small sequential names ("__asyncpg_stmt_1__", ...) per connection, which
    collide the instant two pooled connections land on the same backend.
    Unique names make collision impossible regardless of which backend the
    pooler hands back.

SQLAlchemy's own docs on the second and third:
https://docs.sqlalchemy.org/en/20/dialects/postgresql.html#prepared-statement-name-with-pgbouncer

Do not remove any of the three while DATABASE_URL points at port 6543.

TRIED AND REVERTED — the session pooler (port 5432 on the same
`*.pooler.supabase.com` host) was tested here as a fix for transaction-pooler
latency (see git history around this docstring): it avoids the prepared-
statement problem above by holding one dedicated backend connection per
client session, and an isolated benchmark measured ~80ms per query vs ~700ms
on the transaction pooler. But under real production load (this API's pool
PLUS app/worker.py's own separate pool, both holding session-pooler
connections at once) it got progressively SLOWER across repeated real
requests (837ms -> 1363ms -> 2008ms) instead of settling fast — almost
certainly the session pooler's much lower concurrent-connection ceiling
(unlike the transaction pooler, which multiplexes many clients over few
backend connections) being contended by this project's Nano compute tier.
Reverted back to the transaction pooler + these connect_args as the known-
working baseline. If revisiting: the session pooler is worth another look
ONLY after confirming its actual per-project connection ceiling and either
upgrading compute or serializing api/worker to share one small pool.
"""

import uuid
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

engine = create_async_engine(
    settings.database_url,
    # Echo every SQL statement to the terminal when DEBUG=true. Leave this on
    # while learning — watching the generated SQL is the fastest way to build an
    # intuition for what the ORM is actually doing.
    echo=settings.debug,
    # See the module docstring — all three are required together.
    connect_args={
        "statement_cache_size": 0,
        "prepared_statement_cache_size": 0,
        "prepared_statement_name_func": lambda: f"__asyncpg_{uuid.uuid4()}__",
    },
    # Client-side pool sits in front of Supavisor. Small on purpose: the pooler
    # is doing the heavy lifting, and Supabase's free tier caps total connections.
    pool_size=5,
    max_overflow=10,
    # Verify a connection is alive before handing it out. Costs a trivial
    # round-trip and prevents "server closed the connection unexpectedly" after
    # an idle period — which WILL happen, because the pooler reaps idle sessions.
    pool_pre_ping=True,
    # Proactively retire connections before the pooler does it for us.
    pool_recycle=300,
)

# expire_on_commit=False: without it, SQLAlchemy invalidates every ORM attribute
# after commit, so reading `obj.id` to build the response would fire a fresh
# SELECT — or raise, because the session is already closing. This is the single
# most common source of "greenlet_spawn has not been called" errors in async
# FastAPI apps.
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: one session per request, always closed.

    Commits are explicit in the CRUD helpers rather than automatic here, so a
    handler that raises midway leaves nothing half-written.
    """
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
