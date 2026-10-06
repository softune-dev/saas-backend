-- =============================================================================
-- One-time free AI image allowance for the dashboard onboarding "Site images"
-- step (hero / why-choose-us / about). Not a purchasable balance and not part
-- of the credit ledger: it never touches ai_image_credits, so a merchant's
-- paid credits and this gift can't be confused in a support dispute.
--
-- onboarding_free_images_remaining is a plain countdown. It is claimed with
-- ONE atomic UPDATE ... WHERE remaining > 0 (app/ai_images.py's
-- claim_onboarding_free) so two concurrent requests can't both spend the last
-- unit, and released again if the Gemini call fails. The CHECK keeps a bug
-- from ever driving it negative.
--
-- New tenants get settings.onboarding_free_images (app/config.py, 5) set in
-- app/crud.py's create_tenant_owner_and_site. The one-off backfill below
-- gives the same 5 to tenants that are mid-onboarding right now (a site with
-- onboarding_completed_at still NULL) so they aren't left out; everyone who
-- already finished onboarding stays at 0. The backfill only runs together
-- with the column creation, so re-running this file never re-grants images a
-- merchant has already spent.
-- =============================================================================

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'tenants'
          AND column_name = 'onboarding_free_images_remaining'
    ) THEN
        ALTER TABLE tenants
            ADD COLUMN onboarding_free_images_remaining integer NOT NULL DEFAULT 0
            CHECK (onboarding_free_images_remaining >= 0);

        UPDATE tenants t
           SET onboarding_free_images_remaining = 5
         WHERE t.plan <> 'demo'
           AND EXISTS (
               SELECT 1 FROM sites s
                WHERE s.tenant_id = t.id AND s.onboarding_completed_at IS NULL
           );
    END IF;
END
$$;
