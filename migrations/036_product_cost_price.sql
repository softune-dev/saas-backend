-- Merchant's own cost basis per product, for real profit (not just revenue)
-- in analytics. Optional (nullable) — a merchant who never sets it just
-- doesn't get a profit number, never a wrong one from a silent default of 0.
--
-- Never exposed on any public/storefront endpoint — this is the merchant's
-- private cost data, not something a customer or competitor should see (see
-- app/api/public.py's _public_product, which builds its own dict by hand
-- rather than passing the ORM object through, so this column was never at
-- risk of leaking there by default — still worth stating explicitly).
alter table products add column if not exists cost_price_cents integer;
alter table products add constraint ck_products_cost_price_nonneg
  check (cost_price_cents is null or cost_price_cents >= 0);

-- Order history is immutable (see CLAUDE.md rule 8) — profit on a PAST
-- order must use the cost at the time it was sold, not today's cost, same
-- reasoning as name_snapshot/sku_snapshot/unit_price_cents above. Nullable:
-- older order_items (and any item whose product never had a cost set) have
-- no snapshot, and analytics treats that order as excluded from the profit
-- total rather than assuming a cost of 0.
alter table order_items add column if not exists cost_price_cents_snapshot integer;
