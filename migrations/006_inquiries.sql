-- =============================================================================
--  006_inquiries.sql — contact form submissions
-- =============================================================================
--  Run AFTER 005_agency_template.sql.
--
--  WHY THIS EXISTS: the ContactForm block renders on a public site, but nothing
--  in the schema before this let an anonymous visitor submit it. Orders require
--  a real product and an authenticated caller — wrong shape for "someone typed
--  a message into a form." This is the minimal table that fits: no products, no
--  auth, just a message tied to a site.
-- =============================================================================

CREATE TABLE IF NOT EXISTS inquiries (
    id         uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id  uuid        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    site_id    uuid        NOT NULL REFERENCES sites(id)   ON DELETE CASCADE,

    -- Whatever fields that page's ContactForm block was configured to collect
    -- (name, email, phone, message, ...). Free-form because the block registry
    -- lets each site choose a different subset — see app/blocks.py ContactForm.
    data       jsonb       NOT NULL DEFAULT '{}'::jsonb,

    status     text        NOT NULL DEFAULT 'new'
                           CHECK (status IN ('new', 'read', 'archived')),

    created_at timestamptz NOT NULL DEFAULT now()
);

-- INDEX: the admin's inbox — one site, newest first.
CREATE INDEX IF NOT EXISTS idx_inquiries_site_created
    ON inquiries (site_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_inquiries_tenant ON inquiries (tenant_id);

-- INDEX: unread-count badge in the admin panel.
CREATE INDEX IF NOT EXISTS idx_inquiries_site_new
    ON inquiries (site_id)
    WHERE status = 'new';
