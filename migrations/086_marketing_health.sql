-- =============================================================================
-- marketing_connections: tracking health and a Meta test mode.
--
-- Until now a store's server-side Meta events were fire-and-forget: if the
-- access token was wrong or expired, or Meta rejected an event, the only trace
-- was one line in the server log. Merchants found out only after spending on
-- ads that never optimised. These columns make the outcome visible in the
-- dashboard (last event sent, last error, counts).
--
--   test_event_code   the code from Meta Events Manager > Test events. While
--                     set, server events carry it, so they appear in the test
--                     view instead of counting toward real reporting. Not a
--                     credential: it is a short-lived label Meta shows on screen.
--   events_sent       server events Meta accepted
--   events_failed     server events that could not be sent or were rejected
--   last_event_at     when Meta last accepted one
--   last_error        the last failure, trimmed and with any token removed
--   last_error_at     when that failure happened
--
-- Additive only, with defaults, so existing connections keep working untouched.
-- No foreign keys and no new index: the columns are only read and written
-- alongside the connection row, which is already found by site_id.
-- =============================================================================

ALTER TABLE marketing_connections
    ADD COLUMN IF NOT EXISTS test_event_code text,
    ADD COLUMN IF NOT EXISTS events_sent     integer     NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS events_failed   integer     NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS last_event_at   timestamptz,
    ADD COLUMN IF NOT EXISTS last_error      text,
    ADD COLUMN IF NOT EXISTS last_error_at   timestamptz;
