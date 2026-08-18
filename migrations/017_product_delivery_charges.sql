-- Multiple delivery-charge options per product (e.g. "Inside Dhaka: 60,
-- Outside Dhaka: 120"), replacing the old single delivery_charge_cents in
-- the Add/Edit Product UI. That column stays (harmless, still a valid
-- integer field) since nothing reads it destructively; new code uses this
-- list instead.

ALTER TABLE products
    ADD COLUMN IF NOT EXISTS delivery_charges jsonb NOT NULL DEFAULT '[]'::jsonb;
