-- =============================================================================
--  045_help_ticket_numbers_and_replies.sql — real ticket numbers + replies
-- =============================================================================
--  ticket_number: a native Postgres sequence, not a UUID slice (what the
--  dashboard was displaying before — e.g. "#B19796C2", unreadable and not
--  even guaranteed unique-looking to a human). Starts at 1001 so early
--  tickets don't read as "#00001". Formatted as "TKT-01001" etc in
--  app/schemas.py's HelpTicketOut.
--
--  help_ticket_replies: a superadmin's reply. Per the explicit product
--  decision, this is NOT a live chat thread — every reply here is a
--  one-directional email to the ticket's owner (app/mailer.py's
--  ticket_reply_email), stored here only so the superadmin panel has a
--  paper trail of what's already been sent.
-- =============================================================================

CREATE SEQUENCE IF NOT EXISTS help_ticket_number_seq START 1001;

ALTER TABLE help_tickets
    ADD COLUMN IF NOT EXISTS ticket_number integer NOT NULL
        DEFAULT nextval('help_ticket_number_seq') UNIQUE;

CREATE TABLE IF NOT EXISTS help_ticket_replies (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    ticket_id   uuid        NOT NULL REFERENCES help_tickets(id) ON DELETE CASCADE,
    message     text        NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- Superadmin's ticket detail view loads every reply for one ticket, newest
-- tickets list sorts by recency — both need this.
CREATE INDEX IF NOT EXISTS idx_help_ticket_replies_ticket ON help_ticket_replies (ticket_id, created_at);
