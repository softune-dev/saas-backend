# Free plan: design

Status: waiting for approval. Nothing in here is built.

Marks: **[you]** = decided by the owner. **[call]** = my proposal; correct it if wrong.

## Why

Replace the 7-day trial with a Free plan. Free stores give SoftuneBD a large,
honest store count to market. Serious merchants pay because they hit visible
limits and locked features. **[you]**

## Goals

- Nothing is hidden: locked features are visible, blurred, and open an upgrade popup. **[you]**
- The server enforces every limit. The dashboard only displays it.
- Admin sees free and paid customers separately without opening accounts one by one. **[you]**
- Paying merchants never lose anything during the change. **[you]**

## Non-goals

- Editing plans from the admin screen (approach B below).
- Per-merchant exceptions.
- Commission on orders: 0% on every plan. **[you]**

## Anti-goals

- Deleting an active store.
- Taking an order the merchant cannot see.
- A lock that works in the dashboard but not through the API, AI agent or ChatGPT.

## Approaches considered

- **A. One plan table in code. Chosen. [you]** Small, testable, reviewed. Changing a limit needs a deploy.
- **B. Plans in the database, edited in admin.** More flexible, but one wrong number changes live merchants at once.
- **C. Dashboard locks only.** Rejected: bypassed by calling the API.

## The grid

| | Free | Starter | Growth | Business |
|---|---|---|---|---|
| Products | 10 **[you]** | 50 | 500 **[you]** | Unlimited **[you]** |
| Media storage | 100 MB **[you]** | 500 MB | 2 GB | 3 GB |
| Orders per month | 50 **[you]** | Unlimited | Unlimited | Unlimited |
| Stores | 1 **[call]** | 1 **[call]** | 1 **[call]** | 2 **[you]** |
| AI credits per day | 0 **[you]** | 30 **[you]** | 100 **[you]** | 150 **[you]** |
| Sign-up AI setup + 5 free images | Yes **[you]** | Yes | Yes | Yes |
| AI sidebar, Generate buttons, credit packs, ChatGPT | Locked **[you]** (packs, ChatGPT: **[call]**) | Yes | Yes | Yes |
| Chat Assistant on the store | Yes **[you]** | Yes | Yes | Yes |
| Events | 3 (unlocked **[you]**, count **[call]**) | 3 | 10 | 25 |
| Analytics | Basic **[you]** | Basic | Advanced | Advanced |
| Customers | Locked **[you]** | Yes | Yes | Yes |
| Store Sale (POS) | Locked **[you]** | Yes | Yes | Yes |
| Dropship | Locked **[you]** | Coming soon | Coming soon | Coming soon |
| SEO editing, pixels, server tracking | Locked **[you]** | Yes | Yes | Yes |
| Automatic SEO (titles, sitemap) | Yes | Yes | Yes | Yes |
| Fraud controls | Locked **[you]** | Yes | Yes | Yes |
| Basic fake-order blocking | Yes | Yes | Yes | Yes |
| Custom domain | Locked **[you]** | Yes | Yes | Yes |
| Payments | COD, manual, Softune Pay **[you]** | same | + gateways | + gateways |
| Couriers | Steadfast | + eCourier | All | All |
| Theme | Chosen once, at sign-up | Switch to any available theme **[you]** | Switch | Switch |
| "Powered by SoftuneBD" badge | Shown | Removed | Removed | Removed |

Rows with no mark keep today's behaviour or a suggestion the owner accepted.
"Basic" analytics on Free = orders, revenue and visitors for the last 7 days. **[call]**
Starter, Growth and Business analytics stay exactly as they are today. **[call]**

## Prices (taka, total charged)

Per month: Starter 499/475/449/399, Growth 1,499/1,425/1,349/1,199, Business 3,999/3,799/3,599/3,199. **[you]**

| | 1 month | 3 months | 6 months | 12 months |
|---|---|---|---|---|
| Starter | 499 | 1,425 | 2,694 | 4,788 |
| Growth | 1,499 | 4,275 | 8,094 | 14,388 |
| Business | 3,999 | 11,397 | 21,594 | 38,388 |

Stored as integer cents. A renewal adds the period actually paid for, anchored
to the original date as today. Automatic SMS payment matching must match these totals.

## Behaviour

**Order 51. [you]** Checkout switches to WhatsApp ordering with the cart written
into the message. The merchant is told at 40 and 50, and sees how many shoppers
were affected. Months are calendar months in Asia/Dhaka. The count is a stored
monthly counter raised in the same transaction as the order, because deleting
orders must not reset it. Only storefront orders count. Sign-up requires a
WhatsApp number, so the fallback always works. **[call]**

**Paid plan lapses.** It drops to Free instead of being suspended and deleted.
Extra data is kept and locked. The 10 best-selling products stay live and the
rest are hidden from the store. A custom domain keeps working 30 days, then the
store returns to its subdomain. **[call on the last two]**

**Inactivity.** Free accounts: deleted after 30 inactive days **[you]**, emails on
days 15, 23 and 29. Former paying customers: 90 days **[you]**, emails on days 60,
75 and 85. Active = dashboard use, login, product edit or a received order.
Safety, because existing login records are incomplete:
- a new `last_active_at` is written by authenticated requests (at most hourly) and by new orders;
- every clock starts on the day tracking launches;
- an account is deleted only if all its warning emails were sent;
- at most 20 deletions per run; more than that stops the job and alerts the owner;
- owners are saved to Contacts first, as today.

**Wording.** "Free as long as you're active", never "free forever", because inactive stores are deleted.

**Abuse.** One Free store per verified email and per phone number. Phones are
matched, not SMS-verified. Admin can suspend a store in one click. **[call]**

**Stores per account.** `POST /sites` has no limit today; this adds one for every plan.

## How it is enforced

- `app/plans.py` holds the one table: limits, features, prices. The scattered
  dicts (products, storage, events, AI in two places, prices) read from it.
- A `require_feature("...")` dependency guards routes. The AI agent, ChatGPT
  connector and background jobs call the same check in the service layer.
- A locked feature returns 403 with `{code: "plan_locked", feature, needed_plan}`
  so the dashboard opens the right popup wherever it happens.
- Reading locked data returns a short blurred preview (counts and sample rows
  with no real values), so blur cannot be removed in the browser.
- `/auth/me` returns the plan's features and usage; the dashboard draws locks from it.
- Public site config returns `ordering: "checkout" | "whatsapp"` and the badge flag.
- `FREE_PLAN_ENABLED` is off until launch. Unknown plans fall back to Free limits, not Growth.

`tenants.plan` gains `free`. `demo` stays in the database as the read-only plan
for the 10 showcase stores and RIVELLE, shown as **Internal** and left out of
every customer count.

## Admin

Tenants and Users stay separate **[you]**. Removed: Storefronts, Themes, Demo
leads (leads stay in Contacts) **[you]**. Storefront actions still needed move
into the tenant's details. Tenants gets tabs Free, Paid, Internal:

    Free 1,204 | Paid 37 | Internal 11
    Store        Products  Orders/mo  Storage  Last active  Deletes in
    Glow Lab     8/10 !    41/50 !    62 MB    2 days       -
    Bondhu Shop  0/10      0/50       1 MB     24 days      6 days

Overview shows free and paid separately, and: new this week, near a limit,
about to be deleted, upgraded from Free.

## Delivery: five projects, each shipped alone

1. **Rules and enforcement**, switch off. Paying merchants' limits rise here. Activity tracking starts here.
2. **Locks and upgrade popup**, plus badge and WhatsApp ordering in the three themes.
3. **Multi-month billing.**
4. **Admin.**
5. **Landing, llms.txt, emails, sign-up, then launch:** switch on for internal
   accounts, then for new sign-ups. Any trial that exists at that moment becomes Free.
   Trial code and the trial deletion job are removed. The inactivity job starts last.

Other listings (the ChatGPT directory, Meta ads, social profiles) are updated by
the owner from a checklist produced in project 5.

## Testing

Every limit and lock: one test per plan at the boundary, through the API, agent
and connector. Isolation tests for new tenant-owned data. Order limit under
concurrent orders. Deleting orders does not reset the limit. Lapse and upgrade
round trip loses no data. Deletion never removes: an active account, an unwarned
account, a paid account, an internal account. Existing suites stay green with the
switch off and on.

## Left to the builder

File layout, popup copy, blurred sample rows, exact table columns.
