# Free plan, project 2: locks, upgrade popup, theme checkout, badge

**Goal:** A Free merchant sees every feature, locked ones blurred, and any locked click opens one upgrade popup. Storefronts switch to WhatsApp ordering at the limit and show the badge.

**Spec:** docs/superpowers/specs/2026-11-24-free-plan-design.md. Depends on project 1 (`/auth/me` `plan_info`, `plan_locked` 403, config `ordering` and `badge`).

## Constraints

- Dashboard: existing components and classes only (`modal-panel`, `MaskIcon` + `/sidebar/lock.svg`, `PrimaryButton`, `PageHeading`, renewal banner look, the Courier "Unlock" button). No new colours, icons, fonts or decorations.
- Locks apply only when `plan_info.key === "free"`. With no `plan_info` (old backend) or any other plan the dashboard is exactly as today.
- Bangla through the existing `t()`; add missing strings to the language file.
- Never commit the unrelated deleted `public/*.lottie`, `public/sw.js` or `.impeccable`.
- Branch `free-plan` in each repo, commit per task, nothing pushed. Type-check while iterating, one build at the end.
- A locked page never requests the locked data; it may call `locked-preview` for a count.

## Tasks

1. **Core (dashboard).** `plan_info` type, `PLAN_LOCKED_EVENT` and `PlanLockedError` in `lib/api.ts` (popup only for non-GET calls, so background reads never pop up), `usePlan()`, `UpgradeHost`, `LockedOverlay`, sidebar lock icons.
2. **Locked pages.** Customers, Store Sale, Dropship, Fraud, SEO, Domains, the detailed part of Analytics; order screen parts that need fraud or customers.
3. **Locked actions.** AI sidebar input, themes switch, couriers and gateways Unlock, product and event limits, ChatGPT, order-limit banner at 40 and 50.
4. **Billing.** Free plan card with usage, Chat Assistant in every plan, popup opens the existing payment modal.
5. **Themes (3 repos).** `ordering: whatsapp`, the 403 `order_limit` fallback, badge.
6. **Backend gap.** Videos can be listed and deleted in the gallery.
7. Review, type-check, build, screenshots with a Free test account.
