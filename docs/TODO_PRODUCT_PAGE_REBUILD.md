# DONE: Product Add/Edit rebuilt as a full page

Status as of 2026-08-12: **shipped and verified**, not just planned. This file
used to be a pre-work spec; keeping it as a record of what was built and why,
plus follow-on work (see bottom).

## What shipped

- **Real routes**, not a modal: `/products/new` and `/products/[productId]/edit`
  (`app/products/new/page.tsx`, `app/products/[productId]/edit/page.tsx`),
  both thin wrappers around `components/products/product-form-page.tsx`.
- **Layout matches the reference** (`E:\Projects\Personal Projects\ecom\src\
  routes\admin.tsx` lines ~918–1077): header with back arrow + title +
  Cancel/Save, 2-column grid — left = Title, Price/Compare-at, Description,
  Media Gallery (multi-image upload to Cloudinary); right sidebar =
  Organization card (Status, Category, SKU, Stock, Slug) + Variants card.
- **Variants**: flexible types (not a fixed Size/Color enum) — each type has
  a name, an "affects price" toggle, and a chip list of values. When a type
  affects price, each value gets its own price adjustment input.
  `components/products/product-variants-editor.tsx`.
- **Scope decision made**: pooled stock, not per-variant stock (user's
  explicit call). This meant NO migration was needed — variants are stored
  under `Product.attributes.variants` as structured JSON, not a new table.
- **Backend validation added**: `app/products.py`'s `validate_attributes()`,
  wired into both `create_product` and `update_product` in
  `app/api/commerce.py`. Same reasoning as `blocks.py`'s `validate_blocks` —
  an unvalidated JSONB write path is how malformed data reaches a live site.
  Verified rejecting malformed shapes with 422 (e.g. `variants` not a list).
- **Old modal deleted**: `product-form-modal.tsx` is gone; `products-view.tsx`
  and `products-table.tsx` now `router.push()` to the new routes instead.

### A real bug found and fixed along the way

`ProductUpdate` and `CategoryUpdate` in `app/schemas.py` were both missing
their `sku`/`slug` fields (present on the `*Create` schemas, silently absent
from `*Update`). Pydantic drops unknown fields by default, so a PATCH with
`{"sku": "..."}` succeeded with 200 but silently did nothing — no error, just
never persisted. Caught this by testing the actual save flow end-to-end
(browser round-trip, not just curl-the-happy-path) and cross-checking against
a fresh `GET` afterward. Fixed by adding the missing fields to both `Update`
schemas. Worth remembering as a class of bug: **a `*Create`/`*Update` schema
pair drifting apart is invisible in the API docs (both look "valid"), only
shows up as data that silently doesn't save.**

## Verification performed (products)

All against the real backend/DB, not just typecheck:
- Direct curl PATCH proved the `sku` bug, then proved the fix.
- Full browser round-trip: opened `/products/[id]/edit`, changed SKU +
  description via the actual page, submitted, confirmed via fresh `GET` that
  both persisted correctly alongside the untouched variants (Size: S/M/L,
  no price effect; Weight: 250g/+0, 500g/+৳150) — proves editing one field
  doesn't clobber others.
- Full create flow: `/products/new` → real product created with correct
  price_cents conversion, auto-slug, empty variants array — then deleted the
  test row.

---

# DONE: Orders, Dashboard overview, Customers wired to real data

Status as of 2026-08-12 (later same day): **shipped and verified** against the
live API/DB (not browser UI — no browser automation in this session; API
round-trips + `tsc --noEmit` clean).

## What shipped

### Orders (`dashboard/components/orders/`)
- Session-driven site resolution via `useSession().currentSite`.
- List via `listOrders` (limit 100), server-side **status filter**
  (`pending|paid|fulfilled|cancelled|refunded`), client search.
- **No payment column** — real `OrderOut` has no structured payment method.
- **No delete** — backend has no delete endpoint; order history is immutable
  (CLAUDE.md rule 8).
- Status change: inline `<select>` on each row + detail modal confirm, both
  call `updateOrderStatus` (PATCH `{ status }`). Rows that leave the active
  status filter drop out of the local list immediately.
- Detail modal shows customer (from free-form `customer` JSONB), notes, line
  items from `*_snapshot` columns (never live products), and money totals.
- Stats cards: total / pending / fulfilled / revenue over the fetched page —
  **no fabricated trend %**.
- Shared badge: real 5 statuses with distinct colors.

### Dashboard overview (`dashboard/components/dashboard/`)
- Stat cards from real endpoints:
  - Total Products → `listProducts(...).total`
  - Categories → `listCategories(...).length`
  - Total Orders → `listOrders(...).total`
  - Total Revenue → sum of `total_cents` over fetched orders
- No `changePercent` / `lastMonthValue` (same pattern as Categories/Products
  stats).
- Recent orders: last 10 from `listOrders`, real statuses.
- **Sales Analysis chart still mock** — no time-bucket aggregate endpoint yet;
  left in place with a code comment rather than inventing chart series.

### Customers (`dashboard/components/customers/`)
- **No Customer model** (confirmed) — read-only derivation only.
- Fetches orders, dedupes via `lib/order-customer.ts` (prefer email, then
  phone, else per-order key so nameless guests don't collapse).
- Aggregates: order count, total spend (cents), first/last order dates.
- No add / edit / delete UI.
- Stats: total customers, orders placed, repeat buyers, avg LTV — no fake
  trends.

### Shared helper
- `dashboard/lib/order-customer.ts` — `customerName` / `customerEmail` /
  `customerPhone` / `customerKey` for free-form `Order.customer` JSONB.

## Verification performed (orders / dashboard / customers)

Against real site `ananya-test` (site id `6b86149b-…`), JWT minted via
`create_access_token` for the site owner:

1. Created 3 orders against existing product "Structured Jacket":
   - ORD-1001: Ayesha Rahman / ayesha@email.com, qty 1 + shipping → total
     860000 cents, status `pending`
   - ORD-1002: same email (dedupe test), qty 2 → 1700000
   - ORD-1003: Karim Hossain / karim@email.com → 850000
2. PATCH ORD-1001 → `paid`; fresh GET confirmed `status=paid`,
   `total_cents` unchanged, `name_snapshot` still "Structured Jacket".
3. Status filters: `paid=1`, `pending=2`, `all=3`.
4. Invalid status `"Completed"` → **422** (backend validator).
5. Dashboard inputs: products=1, categories=2, orders=3,
   revenue_cents=3410000.
6. Customer derivation expectation: 2 unique emails (Ayesha 2 orders /
   Karim 1) — UI-side only; no extra endpoint.
7. `dashboard` TypeScript: `tsc --noEmit` exit 0.

Test orders ORD-1001…1003 were left in the DB on the test site so the
dashboard pages have real rows to render; they are not cleaned up (no order
delete endpoint).

## Still open / not in this pass

- **Sales Analysis / Top Selling** on the dashboard home — still placeholder
  series; would need either client-side bucketing of all orders or a small
  aggregate endpoint.
- **Order pagination UI** — fetches up to 100; fine for early catalogs, not
  for high volume.
- **Revenue on stats** is summed over the fetched page only (honest about the
  limit; no server-side `SUM(total_cents)` endpoint).
- **Customers** will miss buyers whose only orders fall beyond the 100-row
  fetch window — same ceiling.
- **Analytics / Billing / Help** pages still on mock data (out of scope here).
- **Browser E2E** of the three pages was not run this session (no browser
  tools available); API contract + types were verified. Worth a quick manual
  click-through on `/`, `/orders`, `/customers` after pull.

---

# DONE: Order detail modal redesign + Courier UI stub

Status as of 2026-08-12: **frontend-only**, `tsc --noEmit` exit 0. No
backend/DB changes. No browser visual verification this pass (tools not
available) — manual click-through on `/orders` (open a row) and `/courier`
recommended after pull.

## 1. What shipped (file by file)

### Order detail modal redesign
- **`dashboard/components/orders/order-detail-modal.tsx`** — full visual
  refresh. Status step strip (pending → paid → fulfilled; cancelled/refunded
  terminal banner), customer card with icons, line items with **best-effort
  product thumbnails**, totals panel. Status select + Update status flow
  unchanged. On open, loads `listProducts(siteId, { limit: 100 })` via
  `useSession().currentSite` and maps `item.product_id` →
  `product.images[0].url`; missing product / no image → `ImageOff`
  placeholder (never a broken `<img>`).

### Courier sidebar section (UI only, no network)
- **`dashboard/components/layout/sidebar/nav-config.ts`** — "Courier" after
  Orders, icon `/sidebar/delivery.svg`, href `/courier`.
- **`dashboard/app/courier/page.tsx`** — thin route → `CourierView`.
- **`dashboard/components/courier/index.ts`** — barrel export.
- **`dashboard/components/courier/courier-view.tsx`** — page shell, session
  empty/skeleton, catalog grid. Connection state is **React useState only**
  (resets on refresh; no localStorage).
- **`dashboard/components/courier/courier-data.ts`** — static catalog
  (Steadfast available; Pathao/RedX coming soon) + mask helper + local
  connection type.
- **`dashboard/components/courier/courier-card.tsx`** — card UI:
  connected/not connected/coming soon, masked keys when connected,
  Connect/Disconnect/disabled actions.
- **`dashboard/components/courier/steadfast-connect-modal.tsx`** —
  `FormModal` collecting API Key, Secret Key, optional Base URL + Label.
- **`dashboard/lib/api/courier.ts`** — **typed stub contract only**; every
  function throws `notImplemented(...)`. UI does **not** call these yet.

## 2. Exact API contract (`lib/api/courier.ts`)

All routes scoped under `/sites/{site_id}/couriers`. Cross-tenant → 404.
Secrets never returned after connect — only `api_key_hint`.

### Types

```ts
type CourierProvider = "steadfast" | "pathao" | "redx";
type CourierConnectionStatus = "connected" | "error" | "disabled";

type CourierConnectionOut = {
  id: string;
  site_id: string;
  provider: CourierProvider;
  status: CourierConnectionStatus;
  api_key_hint: string;       // e.g. "••••••a1b2"
  label: string | null;
  last_verified_at: string | null;  // ISO
  created_at: string;               // ISO
  updated_at: string;               // ISO
};

type SteadfastConnectIn = {
  api_key: string;
  secret_key: string;
  base_url?: string;
  label?: string;
};

type PathaoConnectIn = {
  client_id: string;
  client_secret: string;
  username: string;
  password: string;
  label?: string;
};

type RedxConnectIn = {
  api_key: string;
  label?: string;
};
```

### Functions → HTTP

| Function | Method + path | Body | Response |
|----------|---------------|------|----------|
| `listCourierConnections(siteId)` | `GET /sites/{site_id}/couriers` | — | `CourierConnectionOut[]` |
| `listCourierConnectionsPage(siteId, {limit?, offset?})` | same, optional Page envelope | — | `Page<CourierConnectionOut>` |
| `connectSteadfast(siteId, data)` | `POST /sites/{site_id}/couriers/steadfast` | `SteadfastConnectIn` | `CourierConnectionOut` (409 if already connected) |
| `connectPathao(siteId, data)` | `POST /sites/{site_id}/couriers/pathao` | `PathaoConnectIn` | `CourierConnectionOut` (later wave) |
| `connectRedx(siteId, data)` | `POST /sites/{site_id}/couriers/redx` | `RedxConnectIn` | `CourierConnectionOut` (later wave) |
| `disconnectCourier(siteId, connectionId)` | `DELETE /sites/{site_id}/couriers/{connection_id}` | — | 204 |
| `verifyCourierConnection(siteId, connectionId)` | `POST /sites/{site_id}/couriers/{connection_id}/verify` | — | `CourierConnectionOut` |

**Security note for backend:** store `api_key` / `secret_key` encrypted at
rest; never log or return raw secrets. Validate against Steadfast before
persisting on connect.

## 3. Order image lookup — compromises

- `OrderItemOut` has **no** `image_snapshot` / image field by design (rule 8:
  money/name/sku snapshots only). Thumbnails use **current** catalog images.
- If the product was deleted (`product_id` null or gone) or has no images,
  UI shows a neutral placeholder — layout stays intact.
- Catalog fetch is capped at 100 products; line items whose products fall
  outside that window also get placeholders.
- Image may not match what the buyer saw (product photo changed after
  purchase). **Real fix:** optional `image_url_snapshot` (or similar) on
  `order_items` in a future migration — flagged for backend review, **not**
  implemented this pass.

## 4. `tsc --noEmit`

```
exit=0
```

## 5. Deliberately out of scope

- No `app/` routes, migrations, or courier credential storage design beyond
  the TypeScript contract above (encryption is a backend decision).
- Pathao / RedX connect forms — cards disabled as "Coming soon".
- Shipment create/track UI (consignment booking, status sync) — after
  Steadfast connect API exists.
- Wiring `courier-view` to `lib/api/courier.ts` — would only throw today;
  UI uses session memory instead.
- localStorage persistence of credentials — would diverge from real server
  storage; intentionally avoided.
- Browser visual QA — not available this session.
- No edits to Categories/Products/Dashboard/Customers or `commerce.ts`
  exports.
