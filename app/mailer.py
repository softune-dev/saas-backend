"""Real outbound email — the first thing this codebase actually sends.

Everything before this (VAPID/push, notifications) was in-app only. This
uses plain smtplib over Hostinger's SMTP server with the real
support@softunebd.com mailbox, run off the event loop via asyncio.to_thread
since smtplib is a blocking library.

Blank SMTP_USERNAME/PASSWORD -> send_email() logs and returns False instead
of raising, same blank-able convention as every other integration key in
app/config.py — but unlike those, a failed OTP send is NOT swallowed by its
caller (app/api/leads.py raises a clear 502) because the recipient has no
other way to get the code they need to proceed.

All customer-facing templates share _shell() below: one header (logo badge),
one footer (support line + social icons), one font stack. Gmail and Outlook
strip <link>/@font-face font loading entirely and fall back to their own
sans-serif regardless of what's declared here — only WebKit-based clients
(Apple Mail, iOS Mail) and a few others actually render the Google Fonts
import. The FONT_STACK's system fallbacks are what most recipients see; the
Google Fonts <link> is a bonus for the clients that honor it, not something
to rely on.
"""

import asyncio
import html
import logging
import smtplib
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

from app.config import settings

log = logging.getLogger(__name__)

FONT_STACK = "'Manrope',-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif"
FONT_LINK = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link href="https://fonts.googleapis.com/css2?family=Manrope:wght@400;600;700;800&amp;display=swap" '
    'rel="stylesheet">'
)

# Real social links aren't wired up anywhere in the codebase yet (checked
# landing/components/footer.tsx and lib/site.ts) — these are placeholders so
# the footer has the right shape; swap in real URLs once they exist and this
# stays a one-line change.
_SOCIAL_LINKS = [
    ("Facebook", "#", "M13.5 21v-7.9h2.66l.4-3.1h-3.06V8.1c0-.9.25-1.5 1.53-1.5h1.63V3.8"
     "c-.28-.04-1.25-.12-2.38-.12-2.36 0-3.98 1.44-3.98 4.08v2.24H7.5v3.1h2.9V21h3.1z"),
    ("Instagram", "#", "M12 2.16c2.67 0 2.99.01 4.04.06 2.67.12 3.92 1.4 4.04 4.04.05 1.05.06 1.37.06 4.04"
     "s-.01 2.99-.06 4.04c-.12 2.64-1.37 3.92-4.04 4.04-1.05.05-1.37.06-4.04.06s-2.99-.01-4.04-.06"
     "c-2.67-.12-3.92-1.4-4.04-4.04C3.87 13.29 3.86 12.97 3.86 10.3s.01-2.99.06-4.04C4.04 3.62 5.29 2.34 7.96 2.22"
     "9.01 2.17 9.33 2.16 12 2.16zM12 0C9.28 0 8.94.01 7.87.06 3.9.24.24 3.9.06 7.87.01 8.94 0 9.28 0 12"
     "s.01 3.06.06 4.13c.18 3.97 3.84 7.63 7.81 7.81C8.94 23.99 9.28 24 12 24s3.06-.01 4.13-.06"
     "c3.97-.18 7.63-3.84 7.81-7.81.05-1.07.06-1.41.06-4.13s-.01-3.06-.06-4.13C23.76 3.9 20.1.24 16.13.06"
     "15.06.01 14.72 0 12 0zm0 5.84A6.16 6.16 0 1 0 18.16 12 6.16 6.16 0 0 0 12 5.84zm0 10.16A4 4 0 1 1 16 12"
     "a4 4 0 0 1-4 4zm6.4-10.4a1.44 1.44 0 1 1-1.44-1.44 1.44 1.44 0 0 1 1.44 1.44z"),
    ("LinkedIn", "#", "M20.45 20.45h-3.55v-5.57c0-1.33-.02-3.03-1.85-3.03-1.85 0-2.14 1.45-2.14 2.94v5.66H9.35"
     "V9h3.41v1.56h.05c.48-.9 1.64-1.85 3.37-1.85 3.6 0 4.27 2.37 4.27 5.46v6.28zM5.34 7.43a2.06 2.06 0 1 1 0-4.12"
     "2.06 2.06 0 0 1 0 4.12zM7.12 20.45H3.56V9h3.56v11.45z"),
]


def _send_sync(to_email: str, subject: str, html_body: str, text_body: str) -> None:
    # Every template here uses non-ASCII characters (em dashes, emoji) —
    # MIMEText defaults to us-ascii and Header() defaults to encoding only
    # non-ASCII runs, so both need an explicit utf-8 charset or these get
    # mangled in the actually-delivered email (caught by garbled console
    # output during testing, not by anything that would fail loudly).
    msg = MIMEMultipart("alternative")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = formataddr((settings.smtp_from_name, settings.smtp_from_email))
    msg["To"] = to_email
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=10) as server:
        server.login(settings.smtp_username, settings.smtp_password)
        server.sendmail(settings.smtp_from_email, [to_email], msg.as_string())


async def send_email(to_email: str, subject: str, html_body: str, text_body: str) -> bool:
    """Returns False (never raises) if SMTP isn't configured or the send
    fails — callers that need the send to succeed check the return value
    themselves rather than relying on an exception."""
    if not settings.smtp_username or not settings.smtp_password:
        log.warning("mailer: SMTP not configured, skipping send to %s", to_email)
        return False
    try:
        await asyncio.to_thread(_send_sync, to_email, subject, html_body, text_body)
        return True
    except Exception as exc:  # noqa: BLE001 - any SMTP/network failure is a clean False, not a 500
        log.warning("mailer: failed to send to %s: %s", to_email, exc)
        return False


def _social_icons_html() -> str:
    cells = "".join(
        f'<td style="padding:0 6px;">'
        f'<a href="{href}" style="display:inline-block;width:32px;height:32px;border-radius:50%;'
        f'background-color:#FAF9F6;border:1px solid #D4D4D4;text-align:center;line-height:32px;">'
        f'<svg width="14" height="14" viewBox="0 0 24 24" style="vertical-align:middle;">'
        f'<path fill="#6B7280" d="{path}"/></svg></a></td>'
        for _name, href, path in _SOCIAL_LINKS
    )
    return f'<table role="presentation" cellpadding="0" cellspacing="0" style="margin:0 auto;"><tr>{cells}</tr></table>'


def _shell(preheader: str, body_html: str, *, width: int = 520) -> str:
    """Shared wrapper for every customer-facing template: font import, logo
    badge header, card body, footer with support line + social icons."""
    return f"""\
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
{FONT_LINK}
</head>
<body style="margin:0;padding:0;background-color:#F5F5F4;font-family:{FONT_STACK};">
  <span style="display:none;font-size:1px;line-height:1px;max-height:0;max-width:0;opacity:0;overflow:hidden;color:#F5F5F4;">{preheader}</span>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#F5F5F4;padding:48px 16px;">
    <tr>
      <td align="center">
        <table role="presentation" width="{width}" cellpadding="0" cellspacing="0"
               style="width:{width}px;max-width:100%;background-color:#FFFFFF;border-radius:20px;overflow:hidden;box-shadow:0 4px 24px rgba(15,15,15,0.08);">
          <tr>
            <td style="padding:36px 40px 8px 40px;text-align:center;">
              <span style="display:inline-flex;align-items:center;justify-content:center;width:64px;height:64px;
                           border-radius:18px;background-color:#FAF9F6;border:1px solid #D4D4D4;">
                <img src="{settings.email_logo_url}" alt="Softune" style="height:36px;width:auto;vertical-align:middle;" />
              </span>
            </td>
          </tr>
          {body_html}
          <tr>
            <td style="padding:28px 40px 12px 40px;background-color:#FAF9F6;border-top:1px solid #D4D4D4;text-align:center;">
              {_social_icons_html()}
            </td>
          </tr>
          <tr>
            <td style="padding:0 40px 28px 40px;background-color:#FAF9F6;text-align:center;">
              <p style="margin:0;font-size:12px;line-height:1.7;color:#6B7280;">
                Softune — Ecommerce Website Builder for Bangladesh<br />
                <a href="https://www.softunebd.com" style="color:#FF5733;text-decoration:none;font-weight:600;">softunebd.com</a>
                &nbsp;·&nbsp;
                <a href="mailto:support@softunebd.com" style="color:#FF5733;text-decoration:none;font-weight:600;">support@softunebd.com</a>
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""


def otp_email(otp: str, recipient_name: str | None = None) -> tuple[str, str, str]:
    """Returns (subject, html_body, text_body) for the signup OTP email."""
    greeting = f"Hi {recipient_name}," if recipient_name else "Hi,"
    subject = f"Your Softune verification code: {otp}"

    body_html = f"""\
<tr>
  <td style="padding:20px 40px 4px 40px;text-align:center;">
    <p style="margin:0 0 6px 0;font-size:15px;color:#0F0F0F;">{greeting}</p>
    <h1 style="margin:0 0 14px 0;font-size:22px;font-weight:800;color:#0F0F0F;">Verify your email</h1>
    <p style="margin:0 0 24px 0;font-size:14px;line-height:1.6;color:#6B7280;">
      Enter this code to confirm your email and continue setting up your Softune account.
    </p>
  </td>
</tr>
<tr>
  <td style="padding:0 40px 24px 40px;text-align:center;">
    <div style="display:inline-block;background:linear-gradient(180deg,#FFF7F4,#FAF9F6);border:1px solid #FFD7C7;border-radius:14px;padding:18px 36px;">
      <span style="font-size:34px;font-weight:800;letter-spacing:9px;color:#FF5733;">{otp}</span>
    </div>
  </td>
</tr>
<tr>
  <td style="padding:0 40px 32px 40px;text-align:center;">
    <p style="margin:0;font-size:13px;color:#6B7280;">
      This code expires in 10 minutes. If you didn't request this, you can safely ignore this email.
    </p>
  </td>
</tr>
"""
    html_body = _shell(f"Your verification code is {otp}", body_html)
    text_body = (
        f"{greeting}\n\n"
        f"Your Softune verification code is: {otp}\n\n"
        "This code expires in 10 minutes. If you didn't request this, you can safely ignore this email.\n\n"
        "Softune — softunebd.com"
    )
    return subject, html_body, text_body


def ticket_created_email(
    recipient_name: str | None, ticket_number_display: str, subject: str, message: str,
) -> tuple[str, str, str]:
    """Returns (subject, html, text) — sent to the MERCHANT right after
    they open a ticket. Confirms it was received and gives them the real
    ticket number to reference, nothing more (no chat thread — see
    HelpTicketReply's docstring)."""
    greeting = f"Hi {recipient_name}," if recipient_name else "Hi,"
    email_subject = f"[{ticket_number_display}] We've got your ticket — {subject}"
    subject_safe = html.escape(subject)
    message_safe = html.escape(message)

    body_html = f"""\
<tr>
  <td style="padding:20px 40px 28px 40px;">
    <h1 style="margin:0 0 16px 0;font-size:21px;font-weight:800;color:#0F0F0F;text-align:center;">We've got your ticket 🎫</h1>
    <p style="margin:0 0 16px 0;font-size:14px;color:#0F0F0F;">{greeting}</p>
    <p style="margin:0 0 20px 0;font-size:14px;line-height:1.6;color:#6B7280;">
      We've received your support request and a real person will get back to you here by email —
      reply to this thread any time, it goes straight to our support inbox.
    </p>
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
           style="background-color:#FAF9F6;border:1px solid #D4D4D4;border-radius:14px;">
      <tr><td style="padding:18px 20px;">
        <p style="margin:0 0 4px 0;font-size:12px;color:#6B7280;text-transform:uppercase;letter-spacing:0.04em;">Ticket</p>
        <p style="margin:0 0 12px 0;font-size:16px;font-weight:800;color:#FF5733;">{ticket_number_display}</p>
        <p style="margin:0 0 4px 0;font-size:12px;color:#6B7280;text-transform:uppercase;letter-spacing:0.04em;">Subject</p>
        <p style="margin:0 0 12px 0;font-size:14px;font-weight:600;color:#0F0F0F;">{subject_safe}</p>
        <p style="margin:0 0 4px 0;font-size:12px;color:#6B7280;text-transform:uppercase;letter-spacing:0.04em;">Your message</p>
        <p style="margin:0;font-size:14px;color:#0F0F0F;white-space:pre-wrap;">{message_safe}</p>
      </td></tr>
    </table>
  </td>
</tr>
"""
    html_body = _shell(f"Ticket {ticket_number_display} received", body_html)
    text_body = (
        f"{greeting}\n\nWe've received your support request.\n\n"
        f"Ticket: {ticket_number_display}\nSubject: {subject}\n\nYour message:\n{message}\n\n"
        "Reply to this email any time — it goes straight to our support inbox.\n\n"
        "Softune Support — support@softunebd.com"
    )
    return email_subject, html_body, text_body


def ticket_reply_email(
    recipient_name: str | None, ticket_number_display: str, subject: str, reply_message: str,
) -> tuple[str, str, str]:
    """Returns (subject, html, text) — sent when a superadmin replies. One
    outbound message, not a chat bubble — see HelpTicketReply's docstring
    for why."""
    greeting = f"Hi {recipient_name}," if recipient_name else "Hi,"
    email_subject = f"[{ticket_number_display}] Re: {subject}"
    reply_safe = html.escape(reply_message)

    body_html = f"""\
<tr>
  <td style="padding:20px 40px 28px 40px;">
    <h1 style="margin:0 0 16px 0;font-size:21px;font-weight:800;color:#0F0F0F;text-align:center;">A reply to your ticket 💬</h1>
    <p style="margin:0 0 16px 0;font-size:14px;color:#0F0F0F;">{greeting}</p>
    <p style="margin:0 0 20px 0;font-size:14px;line-height:1.6;color:#6B7280;">
      Here's an update on your support request <strong style="color:#FF5733;">{ticket_number_display}</strong>:
    </p>
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
           style="background-color:#FAF9F6;border:1px solid #D4D4D4;border-radius:14px;">
      <tr><td style="padding:18px 20px;">
        <p style="margin:0;font-size:14px;color:#0F0F0F;white-space:pre-wrap;">{reply_safe}</p>
      </td></tr>
    </table>
    <p style="margin:20px 0 0 0;font-size:13px;color:#6B7280;">
      Need anything else? Just reply to this email and we'll pick it up on the same ticket.
    </p>
  </td>
</tr>
"""
    html_body = _shell(f"Update on ticket {ticket_number_display}", body_html)
    text_body = (
        f"{greeting}\n\nHere's an update on your support request {ticket_number_display}:\n\n"
        f"{reply_message}\n\nNeed anything else? Just reply to this email.\n\n"
        "Softune Support — support@softunebd.com"
    )
    return email_subject, html_body, text_body


def contact_email(
    name: str, email: str, phone: str | None, message: str,
) -> tuple[str, str, str]:
    """Returns (subject, html_body, text_body) — sent to the SUPPORT inbox
    (settings.smtp_from_email), for the landing site's platform-level
    "Contact Us" form (not a merchant's own storefront contact form — that's
    app/api/public.py's submit_contact_form, a completely separate, already-
    working, per-tenant thing). Internal/ops email, so it skips the branded
    shell — plain and functional is more useful here than on-brand."""
    subject = f"Contact form — {name}"
    rows = [("Name", name), ("Email", email), ("Phone", phone or "—"), ("Message", message)]
    text_body = "New contact form submission:\n\n" + "\n".join(f"{k}: {v}" for k, v in rows)
    html_rows = "".join(
        f'<tr><td style="padding:6px 12px;color:#6B7280;font-size:13px;vertical-align:top;">{k}</td>'
        f'<td style="padding:6px 12px;color:#0F0F0F;font-size:13px;font-weight:500;white-space:pre-wrap;">{v}</td></tr>'
        for k, v in rows
    )
    html_body = f"""\
<!DOCTYPE html>
<html><body style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;padding:24px;">
<h2 style="color:#0F0F0F;">New contact form submission</h2>
<table role="presentation" cellpadding="0" cellspacing="0">{html_rows}</table>
</body></html>
"""
    return subject, html_body, text_body


def welcome_email(recipient_name: str | None = None) -> tuple[str, str, str]:
    """Returns (subject, html_body, text_body) — sent once, right after a
    lead verifies their OTP (app/api/leads.py's verify_otp). Not the OTP
    email itself; this is the marketing nudge that follows it, aimed at
    getting them to finish the funnel (demo -> purchase request)."""
    greeting = f"Hi {recipient_name}," if recipient_name else "Hi there,"
    subject = "Welcome to Softune — here's what you can build"

    features = [
        ("🎨", "Drag-and-drop store builder", "Launch a real storefront without touching code."),
        ("🤖", "AI-assisted copywriting", "Describe a product, get sellable copy — built into the editor."),
        ("📊", "Real profit analytics", "Actual margin per order, not just visitor counts."),
        ("🎯", "Marketing pixels, done right", "Meta, TikTok, GTM, and server-side conversion tracking — all built in."),
        ("🚚", "Real courier integrations", "Steadfast, Pathao, RedX, eCourier — connected, not promised."),
        ("💳", "Real payment gateways", "SSLCommerz, bKash, Nagad — accept money from day one."),
    ]
    # valign/align as HTML attributes, not just CSS — Outlook's Word rendering
    # engine ignores vertical-align in style= but honors the attribute, which
    # is what was causing the icon/text misalignment in the previous version.
    feature_rows_html = "".join(
        f'<tr>'
        f'<td width="44" valign="top" style="padding:14px 0;">'
        f'<table role="presentation" cellpadding="0" cellspacing="0" width="36" height="36">'
        f'<tr><td width="36" height="36" align="center" valign="middle" '
        f'style="width:36px;height:36px;border-radius:10px;background-color:#FFF2ED;'
        f'font-size:17px;line-height:36px;text-align:center;">{icon}</td></tr>'
        f'</table></td>'
        f'<td valign="top" style="padding:14px 0 14px 14px;">'
        f'<p style="margin:0;font-size:14px;font-weight:700;color:#0F0F0F;">{title}</p>'
        f'<p style="margin:3px 0 0 0;font-size:13px;color:#6B7280;line-height:1.5;">{desc}</p>'
        f'</td></tr>'
        for icon, title, desc in features
    )
    feature_rows_text = "\n".join(f"{icon} {title} — {desc}" for icon, title, desc in features)

    steps = [
        ("1", "Preview a live demo store", "See a real, working storefront before you commit to anything."),
        ("2", "Pick the plan that fits", "Every plan includes the builder, analytics, and integrations above."),
        ("3", "Launch with real payments connected", "SSLCommerz, bKash, Nagad, and courier handoff — wired in, not bolted on later."),
    ]
    steps_rows_html = "".join(
        f'<tr>'
        f'<td width="32" valign="top" style="padding:10px 0;">'
        f'<span style="display:inline-block;width:24px;height:24px;border-radius:50%;background-color:#0F0F0F;'
        f'color:#FFFFFF;font-size:12px;font-weight:700;line-height:24px;text-align:center;">{n}</span></td>'
        f'<td valign="top" style="padding:10px 0 10px 12px;">'
        f'<p style="margin:0;font-size:14px;font-weight:700;color:#0F0F0F;">{title}</p>'
        f'<p style="margin:3px 0 0 0;font-size:13px;color:#6B7280;line-height:1.5;">{desc}</p>'
        f'</td></tr>'
        for n, title, desc in steps
    )
    steps_text = "\n".join(f"{n}. {title} — {desc}" for n, title, desc in steps)

    body_html = f"""\
<tr>
  <td style="padding:20px 40px 8px 40px;text-align:center;">
    <h1 style="margin:0 0 12px 0;font-size:25px;font-weight:800;color:#0F0F0F;">Welcome to Softune 🎉</h1>
    <p style="margin:0;font-size:15px;color:#0F0F0F;">{greeting}</p>
    <p style="margin:8px 0 0 0;font-size:14px;line-height:1.6;color:#6B7280;">
      You're one step closer to a real online store. Here's what's already built and
      waiting for you — and how to get from here to launched.
    </p>
  </td>
</tr>
<tr>
  <td style="padding:20px 40px 8px 40px;">
    <p style="margin:0 0 4px 0;font-size:12px;font-weight:700;color:#FF5733;text-transform:uppercase;letter-spacing:0.06em;">
      What's already built
    </p>
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-top:1px solid #F0EEEA;">
      {feature_rows_html}
    </table>
  </td>
</tr>
<tr>
  <td style="padding:8px 40px 8px 40px;">
    <div style="border-top:1px solid #F0EEEA;"></div>
  </td>
</tr>
<tr>
  <td style="padding:8px 40px 4px 40px;">
    <p style="margin:0 0 4px 0;font-size:12px;font-weight:700;color:#FF5733;text-transform:uppercase;letter-spacing:0.06em;">
      Get started in 3 steps
    </p>
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
      {steps_rows_html}
    </table>
  </td>
</tr>
<tr>
  <td style="padding:20px 40px 32px 40px;text-align:center;">
    <a href="https://www.softunebd.com/pricing"
       style="display:inline-block;background-color:#FF5733;color:#FFFFFF;font-size:15px;font-weight:700;
              padding:14px 36px;border-radius:999px;text-decoration:none;box-shadow:0 6px 16px rgba(255,87,51,0.32);">
      See plans &amp; pricing
    </a>
    <p style="margin:14px 0 0 0;font-size:13px;color:#6B7280;">
      Or reply to this email and we'll walk you through a live demo store ourselves.
    </p>
  </td>
</tr>
"""
    html_body = _shell("Welcome to Softune — here's what's already built for you", body_html)

    text_body = (
        f"{greeting}\n\n"
        "Welcome to Softune! You're one step closer to a real online store. "
        "Here's what's already built and waiting for you:\n\n"
        f"{feature_rows_text}\n\n"
        "Get started in 3 steps:\n"
        f"{steps_text}\n\n"
        "See plans & pricing: https://www.softunebd.com/pricing\n\n"
        "Questions? Just reply to this email — a real person reads it.\n\n"
        "Softune — softunebd.com"
    )
    return subject, html_body, text_body


def purchase_request_email(
    lead_email: str, full_name: str | None, phone: str | None,
    shop_name: str | None, shop_category: str | None, message: str | None,
) -> tuple[str, str, str]:
    """Returns (subject, html_body, text_body) — sent to the SALES inbox
    (settings.smtp_from_email), not the lead. Plain and functional; this
    one's read by a human on your team, not a prospect, so no logo/branding
    needed."""
    subject = f"Purchase request — {shop_name or full_name or lead_email}"
    rows = [
        ("Name", full_name or "—"),
        ("Email", lead_email),
        ("Phone", phone or "—"),
        ("Shop name", shop_name or "—"),
        ("Shop category", shop_category or "—"),
        ("Message", message or "—"),
    ]
    text_body = "New purchase request:\n\n" + "\n".join(f"{k}: {v}" for k, v in rows)
    html_rows = "".join(
        f'<tr><td style="padding:6px 12px;color:#6B7280;font-size:13px;">{k}</td>'
        f'<td style="padding:6px 12px;color:#0F0F0F;font-size:13px;font-weight:500;">{v}</td></tr>'
        for k, v in rows
    )
    html_body = f"""\
<!DOCTYPE html>
<html><body style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;padding:24px;">
<h2 style="color:#0F0F0F;">New purchase request</h2>
<table role="presentation" cellpadding="0" cellspacing="0">{html_rows}</table>
</body></html>
"""
    return subject, html_body, text_body
