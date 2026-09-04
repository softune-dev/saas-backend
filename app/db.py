"""Async database engine and session factory.

READ THIS IF CONNECTIONS MISBEHAVE — which pooler, and why it matters
-----------------------------------------------------------------------
DATABASE_URL must point at Supabase's SESSION pooler (Supavisor, port 5432 on
the `*.pooler.supabase.com` host), not the transaction pooler (port 6543) and
not the direct connection (`db.*.supabase.co`, IPv6-only by default — dead on
an IPv4-only host without paying for Supabase's IPv4 add-on).

The transaction pooler hands your query whatever backend connection is free
at that moment — it does not guarantee the same one twice. asyncpg by default
optimises by PREPARING each statement on the server and reusing it by name;
those prepared statements live on ONE specific backend connection, so on the
transaction pooler you'd get either `InvalidSQLStatementNameError` /
`DuplicatePreparedStatementError`, or — if you disable prepared statements to
avoid that — every single query paying the FULL parse/bind/describe/execute
round trip instead of a cached one-shot bind+execute. Measured impact on this
project's production box: ~700ms per trivial `SELECT 1` on the transaction
pooler vs ~80ms on the session pooler, even though raw network RTT to
Supabase was only ~90ms either way. The pooler choice IS the latency issue,
not geography.

The session pooler avoids both problems: it holds ONE dedicated backend
connection per client session (same guarantee a direct connection gives you),
so prepared statements work normally — while still being reachable over IPv4,
since it's proxied through the same pooler host as the transaction pooler.
The tradeoff is fewer total concurrent connections than the transaction
pooler allows, which is fine here: this app's own client-side pool below caps
out at 15 connections, well under Supabase's session-pooler limit for this
project's compute size.

If DATABASE_URL ever needs to point at the transaction pooler again (e.g. a
serverless/edge deployment making brief, stateless connections — the case
Supabase actually recommends it for), reintroduce these three connect_args
together, not separately — confirmed empirically that any one missing still
fails on literally the first query:

  - "statement_cache_size": 0            (asyncpg's own native cache)
  - "prepared_statement_cache_size": 0   (SQLAlchemy's asyncpg dialect wrapper's
                                           separate bookkeeping — the one most
                                           blog posts actually mean)
  - "prepared_statement_name_func"       (unique names per statement, so two
                                           pooled connections landing on the
                                           same backend can't collide)

SQLAlchemy's own docs on the last two:
https://docs.sqlalchemy.org/en/20/dialects/postgresql.html#prepared-statement-name-with-pgbouncer
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

engine = create_async_engine(
    settings.database_url,
    # Echo every SQL statement to the terminal when DEBUG=true. Leave this on
    # while learning — watching the generated SQL is the fastest way to build an
    # intuition for what the ORM is actually doing.
    echo=settings.debug,
    # Client-side pool sits in front of Supavisor's session pooler. Small on
    # purpose: the session pooler holds one dedicated backend connection per
    # entry in this pool for its whole lifetime, and Supabase caps total
    # pooler connections per project.
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
