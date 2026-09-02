"""Branded invoice HTML, rendered to PDF by the worker via Playwright (see
queue.JOB_GENERATE_INVOICE_PDF and app/worker.py's handler) — this module
only builds the HTML string; it has no Playwright/network code itself, so
it's cheap to unit-test or preview by just calling invoice_html() directly.

Reuses app/mailer.py's brand constants so an invoice and an email look like
they came from the same company, not two different templates.
"""

import html

from app.config import settings
from app.mailer import BRAND, INK, LINE, MUTED, SURFACE

# Real plan lineup — mirrors dashboard/components/billing/billing-data.ts
# and landing/components/pricing.tsx exactly (same ids, same BDT prices).
# There is no payment gateway yet; this is the price an invoice records
# when the team manually switches a tenant onto a paid plan, not something
# a merchant self-serves into. Money as integer cents (CLAUDE.md rule 7).
PLAN_PRICES_CENTS: dict[str, int] = {
    "trial": 0,
    "demo": 0,
    "starter": 119_000,
    "growth": 299_000,
    "business": 699_000,
}

PLAN_NAMES: dict[str, str] = {
    "trial": "Trial",
    "demo": "Demo",
    "starter": "Starter",
    "growth": "Growth",
    "business": "Business",
}


def _format_money(cents: int, currency: str) -> str:
    symbol = "৳" if currency == "BDT" else ""
    amount = f"{cents / 100:,.2f}"
    return f"{symbol}{amount}" if symbol else f"{amount} {currency}"


def invoice_html(
    *,
    invoice_number: str,
    plan: str,
    amount_cents: int,
    currency: str,
    period_label: str,
    issued_at: str,
    tenant_name: str,
    tenant_business: dict,
    owner_name: str | None = None,
    owner_email: str | None = None,
    owner_phone: str | None = None,
    site_domain: str | None = None,
) -> str:
    """A single printable A4-ish page — deliberately not wrapped in
    mailer.py's email _shell() (that's a 560px email card; this is a
    document meant to be read full-width and printed/downloaded).

    Every field here comes from a real column (tenant_business is the
    snapshot captured at issue time, owner_* comes from the tenant's owner
    User row, site_domain from their live Site) — there's no invented
    street address or tax registration for either side. Softune itself has
    no fixed business address to print, so the sender block stays limited
    to what's real: brand, support contact, website."""
    plan_name = PLAN_NAMES.get(plan, plan.title())
    amount_display = _format_money(amount_cents, currency)
    number_safe = html.escape(invoice_number)
    tenant_safe = html.escape(tenant_name)
    issued_safe = html.escape(issued_at)
    logo_url = html.escape(settings.email_logo_url)

    legal_name = tenant_business.get("legal_name") or tenant_name
    trade_name = tenant_business.get("trade_name")
    business_type = tenant_business.get("business_type")
    trade_license = tenant_business.get("trade_license")
    tin = tenant_business.get("tin")
    billing_email = tenant_business.get("billing_email")

    bill_to_lines = [f'<span style="font-weight:600;">{html.escape(str(legal_name))}</span>']
    if trade_name and trade_name != legal_name:
        bill_to_lines.append(html.escape(str(trade_name)))
    if owner_name:
        bill_to_lines.append(html.escape(owner_name))
    if site_domain:
        bill_to_lines.append(html.escape(site_domain))
    contact_email = billing_email or owner_email
    if contact_email:
        bill_to_lines.append(html.escape(str(contact_email)))
    if owner_phone:
        bill_to_lines.append(html.escape(owner_phone))
    bill_to_html = "<br />".join(bill_to_lines)

    detail_lines = []
    if business_type:
        detail_lines.append(("Business type", str(business_type).replace("_", " ").title()))
    if trade_license:
        detail_lines.append(("Trade license", str(trade_license)))
    if tin:
        detail_lines.append(("TIN", str(tin)))
    detail_rows_html = "".join(
        f'<p style="margin:0 0 4px 0;font-size:12px;color:{MUTED};">'
        f'<span style="text-transform:uppercase;letter-spacing:0.03em;font-size:10px;">{html.escape(label)}</span> '
        f'{html.escape(value)}</p>'
        for label, value in detail_lines
    )

    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<style>
  @page {{ size: A4; margin: 0; }}
  body {{
    margin: 0; padding: 56px 64px; background: {SURFACE};
    font-family: 'Manrope', -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif;
    color: {INK};
  }}
  .row {{ display: flex; justify-content: space-between; align-items: flex-start; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 28px; }}
  th {{
    text-align: left; font-size: 11px; letter-spacing: 0.04em; text-transform: uppercase;
    color: {MUTED}; padding: 10px 0; border-bottom: 1px solid {LINE};
  }}
  th.num, td.num {{ text-align: right; }}
  td {{ padding: 14px 0; border-bottom: 1px solid {LINE}; font-size: 14px; }}
  .muted {{ color: {MUTED}; }}
  .brand {{ color: {BRAND}; }}
  .label {{ margin:0 0 8px 0; font-size:11px; letter-spacing:0.04em; text-transform:uppercase; color:{MUTED}; }}
</style>
</head>
<body>
  <div class="row" style="border-bottom:2px solid {INK};padding-bottom:28px;">
    <div>
      <img src="{logo_url}" alt="Softune" width="32" height="32" style="display:block;border-radius:7px;" />
      <p style="margin:14px 0 0 0;font-size:20px;font-weight:700;">Softune</p>
      <p class="muted" style="margin:4px 0 0 0;font-size:12px;line-height:1.6;">
        Ecommerce platform for Bangladesh<br />
        softunebd.com · support@softunebd.com
      </p>
    </div>
    <div style="text-align:right;">
      <p style="margin:0;font-size:26px;font-weight:700;letter-spacing:0.02em;">Invoice</p>
      <p class="brand" style="margin:8px 0 0 0;font-size:15px;font-weight:700;">{number_safe}</p>
      <table style="width:auto;margin:14px 0 0 auto;">
        <tr>
          <td style="border:none;padding:2px 0;text-align:left;font-size:12px;" class="muted">Date of issue</td>
          <td style="border:none;padding:2px 0 2px 24px;text-align:right;font-size:12px;">{issued_safe}</td>
        </tr>
        <tr>
          <td style="border:none;padding:2px 0;text-align:left;font-size:12px;" class="muted">Plan</td>
          <td style="border:none;padding:2px 0 2px 24px;text-align:right;font-size:12px;">{html.escape(plan_name)}</td>
        </tr>
        <tr>
          <td style="border:none;padding:2px 0;text-align:left;font-size:12px;" class="muted">Billing period</td>
          <td style="border:none;padding:2px 0 2px 24px;text-align:right;font-size:12px;">{html.escape(period_label)}</td>
        </tr>
      </table>
    </div>
  </div>

  <div class="row" style="margin-top:32px;">
    <div>
      <p class="label">Bill to</p>
      <p style="margin:0;font-size:14px;line-height:1.7;">{bill_to_html}</p>
    </div>
    <div style="text-align:right;">
      <p class="label">Account</p>
      <p style="margin:0 0 8px 0;font-size:14px;">{tenant_safe}</p>
      {detail_rows_html}
    </div>
  </div>

  <p style="margin:36px 0 0 0;font-size:22px;font-weight:700;">
    {amount_display} <span class="muted" style="font-size:14px;font-weight:400;">{html.escape(currency)}</span>
  </p>

  <table>
    <thead>
      <tr>
        <th>Description</th>
        <th>Period</th>
        <th class="num">Amount</th>
      </tr>
    </thead>
    <tbody>
      <tr>
        <td>Softune — {html.escape(plan_name)} plan</td>
        <td class="muted">{html.escape(period_label)}</td>
        <td class="num">{amount_display}</td>
      </tr>
    </tbody>
  </table>

  <div class="row" style="margin-top:6px;">
    <div></div>
    <table style="width:260px;margin-top:0;">
      <tr>
        <td class="muted" style="border-bottom:1px solid {LINE};">Subtotal</td>
        <td class="num" style="border-bottom:1px solid {LINE};">{amount_display}</td>
      </tr>
      <tr>
        <td style="border-bottom:none;font-weight:700;padding-top:14px;">Amount due</td>
        <td class="num" style="border-bottom:none;font-weight:700;padding-top:14px;">{amount_display}</td>
      </tr>
    </table>
  </div>

  <div style="margin-top:64px;padding-top:20px;border-top:1px solid {LINE};">
    <p class="muted" style="margin:0;font-size:12px;line-height:1.7;">
      Questions about this invoice? support@softunebd.com · softunebd.com
    </p>
  </div>
</body>
</html>
"""
