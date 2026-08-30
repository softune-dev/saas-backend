-- =============================================================================
--  040_courier_ecourier_provider.sql — allow 'ecourier' as a courier provider
-- =============================================================================
--  eCourier publishes a real public API spec (username/password auth — see
--  app/api/courier.py's connect_ecourier for why there's no live verify
--  call). Paperfly is deliberately NOT added here — the only public
--  reference found for Paperfly's auth was a third-party package with what
--  looks like someone else's hardcoded secret, not a documented protocol,
--  so it's not safe to build against.
-- =============================================================================

ALTER TABLE courier_connections
    DROP CONSTRAINT IF EXISTS courier_connections_provider_check;

ALTER TABLE courier_connections
    ADD CONSTRAINT courier_connections_provider_check
    CHECK (provider IN ('steadfast', 'pathao', 'redx', 'ecourier'));
