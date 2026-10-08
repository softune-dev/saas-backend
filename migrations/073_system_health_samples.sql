-- =============================================================================
-- System health samples: one row per API process per minute, so the superadmin
-- dashboard can draw load, latency and error curves over time instead of only
-- showing "up / down right now".
--
-- The API process counts every request in memory (app/metrics.py) and, once a
-- minute, writes one row here: request count, 5xx count, latency percentiles,
-- the slowest routes, plus a ping of the database / Redis / RabbitMQ and the
-- job queue depth. Rows are tiny (a few hundred bytes) and the sampler deletes
-- anything older than 30 days, so this never grows without bound.
--
-- Not tenant data: no tenant_id, only ever read through /superadmin/health.
-- =============================================================================

CREATE TABLE IF NOT EXISTS system_health_samples (
    id            uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    sampled_at    timestamptz NOT NULL DEFAULT now(),
    requests      integer     NOT NULL DEFAULT 0,
    errors_5xx    integer     NOT NULL DEFAULT 0,
    p50_ms        integer,
    p95_ms        integer,
    max_ms        integer,
    db_ms         integer,
    redis_ms      integer,
    rabbit_ok     boolean,
    queue_depth   integer,
    top_routes    jsonb       NOT NULL DEFAULT '[]'::jsonb
);

-- INDEX: the health curves read a time window, newest last, and the sampler's
-- retention DELETE filters on the same column.
CREATE INDEX IF NOT EXISTS idx_health_samples_at
    ON system_health_samples (sampled_at DESC);
