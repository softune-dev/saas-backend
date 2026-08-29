-- Distinguishes a real storefront checkout from a merchant-entered walk-in
-- sale (POS v1) — both go through the same order pipeline (app/api/
-- commerce.py's create_order for POS, app/api/public.py's
-- create_public_order for the storefront), just tagged differently so
-- Orders/Analytics can show Online vs In-Person instead of only ever
-- looking like storefront traffic.
alter table orders add column if not exists channel text not null default 'storefront';
alter table orders add constraint ck_orders_channel check (channel in ('storefront', 'pos'));
