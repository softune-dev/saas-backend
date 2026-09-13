-- =============================================================================
--  062_event_popup.sql — "show this event as a homepage popup" flag
-- =============================================================================
--  A merchant can mark at most one of their events as the popup shown on
--  the storefront (independent of whether that event is also featured in
--  the homepage Events section — see sections/EventsSection.tsx's own
--  selectedEventIds, a completely separate concern). Turning is_popup on
--  for one event unsets it on any other event for that site — enforced at
--  the application layer in app/api/events.py; the partial unique index
--  below is the database-level backstop so a race or a bug can never leave
--  two events marked true at once.
-- =============================================================================

ALTER TABLE events ADD COLUMN IF NOT EXISTS is_popup boolean NOT NULL DEFAULT false;

-- INDEX + CONSTRAINT: at most one popup event per site. Partial so it costs
-- nothing on the overwhelming majority of rows (is_popup = false), and
-- doubles as the lookup index for "which event (if any) is this site's
-- popup" on the public storefront.
CREATE UNIQUE INDEX IF NOT EXISTS uq_events_site_popup
    ON events (site_id)
    WHERE is_popup;
