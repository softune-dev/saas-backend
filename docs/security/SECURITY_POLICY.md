# Softune security policy

How Softune protects merchant and shopper data. Short on purpose: every rule
here is one the team can actually follow. Review it every six months and after
every incident. Owner: the founder until a dedicated security owner exists.

## 1. What we protect

| Class | Examples | Rule |
|---|---|---|
| **Secret** | `SECRET_KEY`, `COURIER_CREDENTIALS_KEY`, DB password, Supabase service-role key, Cloudinary/Vercel/Gemini/WhatsApp/SMTP keys, merchants' gateway & courier credentials | Only in the production `.env` / encrypted columns. Never in git, chat, email, screenshots or logs. |
| **Confidential** | Shopper names, phones, addresses, IPs; orders; merchant legal/TIN details; invoices | Only through the API with tenant scoping. Never copied to personal devices or spreadsheets except a merchant exporting their own data. |
| **Internal** | Analytics, AI usage, audit logs | Superadmin only. |
| **Public** | Published storefront content | — |

We never store card numbers or wallet PINs. Payments happen on the gateway's
own page (bKash, Nagad, SSLCommerz). Keep it that way — it keeps Softune out
of PCI DSS card-data scope.

## 2. Access control

- **Least privilege.** Merchants: owner/admin can change money, domains,
  credentials and delete data; members only operate the store (see audit
  H10). Superadmin is for Softune staff only.
- **Superadmin accounts** need an authenticator app (TOTP) once built, a
  unique password from a password manager, and are reviewed monthly. Remove
  the flag the day someone leaves, then revoke their tokens
  (`revoke_all_user_tokens`).
- **Infrastructure** (VM SSH, Supabase, Cloudinary, Vercel, Google Cloud,
  Meta, domain registrar, GitHub): 2FA mandatory on every account, SSH by key
  only (password login disabled), no shared logins. Keep an access list and
  review it quarterly.
- **Offboarding checklist:** remove from GitHub org, Supabase, Vercel,
  Cloudinary, VM `authorized_keys`, superadmin flag; rotate any secret they
  could have seen.

## 3. Secrets

- Generated with `openssl rand -hex 32` (or the provider's console).
- Stored only in the production `.env` on the VM (mode `600`, owned by the
  deploy user) and a password manager vault. Never committed — `.env*` is
  git-ignored; CI runs `gitleaks` (to add).
- **Rotate** immediately if exposed or after staff with access leave;
  otherwise yearly. `SECRET_KEY` rotation logs everyone out — announce it.
  `COURIER_CREDENTIALS_KEY` rotation needs a re-encrypt script — do not just
  replace it.
- If a secret is pushed to git by mistake: rotate first, then clean history.
  Rewriting history alone does not un-leak it.

## 4. Building software safely

Every pull request touching the backend must keep the `CLAUDE.md` rules, plus:

1. Tenant-owned reads/writes go through `crud.get_scoped`/`list_scoped`; any
   id taken from a request body (parent, category, product, customer) is
   re-fetched with `get_scoped` before use.
2. New endpoints decide explicitly: public, any user, or `AdminUser`.
3. Anything that marks money as received is verified server-to-server **and**
   matched to the order, amount and currency.
4. Any HTML from users, shoppers or AI is escaped, or sanitized with an
   allowlist; no new `dangerouslySetInnerHTML` without a sanitizer.
5. Any outbound request to a URL a user can influence (webhooks, images,
   headless browser) blocks private/loopback/link-local addresses.
6. Secrets are compared with `hmac.compare_digest`; tokens stored as hashes.
7. New tenant tables get isolation tests in `tests/test_tenant_isolation.py`
   and an RLS-enable line in their migration.
8. Dependencies: CI runs `pip-audit`, `npm audit`, `bandit`; Dependabot is on;
   critical advisories are patched within 7 days, high within 30.

## 5. Logging and monitoring

- Log security events: failed logins, OTP failures and lockouts, password
  changes, token revocations, webhook signature failures, superadmin actions
  (`admin_audit_logs`), payment verification mismatches.
- Never log passwords, OTPs, tokens, full gateway responses with secrets, or
  full shopper addresses.
- Alert (email/WhatsApp to the on-call person) on: Redis or RabbitMQ down,
  error rate spikes, any `SECURITY:` startup line, bursts of payment
  verification mismatches or failed logins.
- Keep application logs 90 days; audit logs at least 1 year.

## 6. Backups and recovery

- Supabase point-in-time recovery enabled; plus a weekly encrypted
  `pg_dump` stored outside Supabase. Dumps are Secret-class data (`*.dump` is
  git-ignored for that reason).
- Test a restore every quarter into a throwaway project and write down how
  long it took. Target: lose at most 24 hours of data; back online within 8
  hours.
- Cloudinary media and the VM's `caddy_data` volume (TLS certificates) are
  included in the plan.

## 7. Incident response

1. **Contain** (minutes): revoke tokens / rotate the exposed secret / block
   the IP at Caddy / take the affected feature offline.
2. **Assess** (hours): what data, which tenants, since when. Preserve logs
   before they rotate.
3. **Fix and verify** the root cause; add a test so it can't come back.
4. **Notify** affected merchants in plain language within 72 hours of
   confirming an incident that touched their data, with what happened, what
   we did, and what they should do. Bangladesh's cyber-security and data
   protection rules have been changing (ordinances in 2025); confirm current
   reporting duties with a Bangladeshi lawyer and record the answer here.
5. **Write up** a short blameless post-mortem in `docs/security/`.

## 8. Data retention and privacy

- Already enforced in code: notifications deleted after 15 days; unpaid
  trials deleted after the grace period; abandoned paid accounts swept.
- Add: page-view rows older than 13 months aggregated or deleted; order IP
  addresses cleared after 12 months (they are only for fraud checks).
- Merchants can export their data and request deletion; on deletion remove
  the tenant, its Cloudinary folder, and its invoices.
- Publish a privacy policy listing the processors below.

## 9. Third parties (processors)

Supabase (database), Cloudinary (media, invoices), Vercel (storefront
hosting), Google (Gemini AI, reCAPTCHA), Meta (WhatsApp, Conversions API),
OpenAI (ChatGPT connector, only when a merchant connects it), Hostinger
(SMTP), bKash / Nagad / SSLCommerz (payments), Steadfast / Pathao / RedX /
eCourier (delivery). Review this list when adding an integration; prefer
vendors with 2FA and audit logs, and give each the narrowest key possible.

## 10. Reporting a vulnerability

Email **security@softunebd.com** (set this mailbox up) with steps to
reproduce. We reply within 3 working days and do not take legal action
against good-faith research that avoids real merchants' data, doesn't degrade
service, and gives us reasonable time to fix before disclosure. Publish this
section on the website, and add `/.well-known/security.txt` to the landing
site pointing to it.
