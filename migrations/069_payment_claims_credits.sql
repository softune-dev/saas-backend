-- =============================================================================
-- Widens payment_claims (migrations/068) to also cover AI image / chat
-- credit-pack purchases, not just plan upgrades, and adds the notification
-- types the dashboard bell/toast needs to announce an auto-verified payment
-- the instant it happens.
--
-- Until now app/api/ai_images.py's submit_credit_purchase and
-- app/api/ai.py's submit_chat_credit_purchase only ever sent an email —
-- nothing for app/api/public.py's bkash_sms_webhook to match against. A
-- `kind` discriminator lets the same table and the same webhook serve all
-- three purchase types: 'plan' (existing behavior, plan + no pack_id/
-- credits), or 'image_credits'/'chat_credits' (pack_id + credits, no plan).
-- =============================================================================

ALTER TABLE payment_claims ALTER COLUMN plan DROP NOT NULL;
ALTER TABLE payment_claims ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'plan';
ALTER TABLE payment_claims ADD COLUMN IF NOT EXISTS pack_id TEXT;
ALTER TABLE payment_claims ADD COLUMN IF NOT EXISTS credits INTEGER;

ALTER TABLE payment_claims DROP CONSTRAINT IF EXISTS payment_claims_kind_check;
ALTER TABLE payment_claims
    ADD CONSTRAINT payment_claims_kind_check
    CHECK (kind IN ('plan', 'image_credits', 'chat_credits'));

-- A 'plan' claim must carry a plan and no pack fields; a credits claim must
-- carry both pack fields and no plan — keeps the webhook's branch-by-kind
-- logic from ever reading a NULL it didn't expect.
ALTER TABLE payment_claims DROP CONSTRAINT IF EXISTS payment_claims_kind_fields_check;
ALTER TABLE payment_claims
    ADD CONSTRAINT payment_claims_kind_fields_check
    CHECK (
        (kind = 'plan' AND plan IS NOT NULL AND pack_id IS NULL AND credits IS NULL)
        OR (kind IN ('image_credits', 'chat_credits')
            AND plan IS NULL AND pack_id IS NOT NULL AND credits IS NOT NULL)
    );

-- Widening the CHECK constraint 019/021_*.sql created — Postgres has no
-- ALTER CHECK, so drop and recreate with the two new values. Fired the
-- instant app/api/public.py's bkash_sms_webhook auto-verifies a claim, so
-- the dashboard bell/toast (5s poll, dashboard/lib/api/notifications.ts)
-- can announce it without the merchant refreshing the page.
ALTER TABLE notifications
    DROP CONSTRAINT IF EXISTS notifications_type_check;

ALTER TABLE notifications
    ADD CONSTRAINT notifications_type_check
    CHECK (type IN (
        'order_created', 'order_blocked', 'site_published', 'site_unpublished',
        'payment_verified', 'credit_purchase_verified'
    ));
