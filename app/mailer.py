"""Real outbound email — the first thing this codebase actually sends.

Everything before this (VAPID/push, notifications) was in-app only. This
uses plain smtplib over Hostinger's SMTP server with the real
support@softunebd.com mailbox, run off the event loop via asyncio.to_thread
since smtplib is a blocking library.

Blank SMTP_USERNAME/PASSWORD -> send_email() logs and returns False instead
of raising, same blank-able convention as every other integration key in
app/config.py — but unlike those, a failed OTP send is NOT swallowed by its
caller because the recipient has no other way to get the code they need.

All customer-facing templates share _shell() below. Gmail and Outlook
strip <link>/@font-face; the FONT_STACK's system fallbacks are what most
recipients see. The logo is a remote <img>, not a CID attachment — Gmail's
inbox list treats CID images as attachment chips under the subject.

Colors match the landing site (brand #FF5A36, ink #171717, canvas #EAEAEA).
Layout is table-based and inline-styled because that's what actually
survives Gmail/Outlook; rounded pills and 16px cards are progressive
enhancement, not something Outlook is expected to honor.
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
    '<link href="https://fonts.googleapis.com/css2?family=Manrope:wght@400;500&amp;display=swap" '
    'rel="stylesheet">'
)

BRAND = "#FF5A36"
INK = "#171717"
MUTED = "#6B7280"
CANVAS = "#EAEAEA"
SURFACE = "#FFFFFF"
LINE = "#E5E7EB"
SOFT = "#F4F4F5"
SITE = "https://www.softunebd.com"
DASHBOARD = "https://dashboard.softunebd.com"
SUPPORT = "support@softunebd.com"


def _send_sync(to_email: str, subject: str, html_body: str, text_body: str) -> None:
    # Every template here uses non-ASCII characters (em dashes) —
    # MIMEText defaults to us-ascii and Header() defaults to encoding only
    # non-ASCII runs, so both need an explicit utf-8 charset or these get
    # mangled in the actually-delivered email.
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


RADIUS = "4px"


def _btn(href: str, label: str) -> str:
    return (
        f'<a href="{href}" style="display:inline-block;background-color:{BRAND};'
        f'color:#FFFFFF;font-size:14px;font-weight:400;'
        f'padding:12px 22px;border-radius:{RADIUS};text-decoration:none;">{label}</a>'
    )


def _shell(preheader: str, body_html: str, *, width: int = 560) -> str:
    """Shared wrapper: logo header, card body, quiet footer. Left-aligned
    copy — centered-everything emails read as 2016 templates."""
    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light">
<meta name="supported-color-schemes" content="light">
{FONT_LINK}
</head>
<body style="margin:0;padding:0;background-color:{CANVAS};font-family:{FONT_STACK};-webkit-font-smoothing:antialiased;">
  <span style="display:none;font-size:1px;line-height:1px;max-height:0;max-width:0;opacity:0;overflow:hidden;color:{CANVAS};">{preheader}&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;</span>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:{CANVAS};padding:40px 16px;">
    <tr>
      <td align="center">
        <table role="presentation" width="{width}" cellpadding="0" cellspacing="0"
               style="width:{width}px;max-width:100%;background-color:{SURFACE};border-radius:{RADIUS};overflow:hidden;">
          <tr>
            <td style="padding:28px 36px 0 36px;">
              <img src="{settings.email_logo_url}" alt="Softune" width="36" height="36" style="display:block;height:36px;width:36px;border:0;" />
            </td>
          </tr>
          {body_html}
          <tr>
            <td style="padding:8px 36px 28px 36px;">
              <p style="margin:0;font-size:12px;line-height:1.7;color:{MUTED};">
                Softune · Ecommerce for Bangladesh<br />
                <a href="{SITE}" style="color:{BRAND};text-decoration:none;">softunebd.com</a>
                &nbsp;·&nbsp;
                <a href="mailto:{SUPPORT}" style="color:{BRAND};text-decoration:none;">{SUPPORT}</a>
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
    """Signup / login verification code."""
    greeting = f"Hi {html.escape(recipient_name)}," if recipient_name else "Hi,"
    greeting_text = f"Hi {recipient_name}," if recipient_name else "Hi,"
    subject = f"Your Softune code: {otp}"
    digits = "".join(c for c in otp if c.isdigit())[:6].ljust(6)
    cells = []
    for i, d in enumerate(digits):
        cells.append(
            f'<td width="44" height="52" align="center" valign="middle" '
            f'style="width:44px;height:52px;background-color:{SOFT};border:1px solid {LINE};'
            f'border-radius:{RADIUS};font-size:22px;font-weight:400;color:{INK};font-family:ui-monospace,Menlo,Consolas,monospace;">'
            f"{html.escape(d)}</td>"
        )
        if i < 5:
            cells.append('<td width="8" style="width:8px;font-size:0;line-height:0;">&nbsp;</td>')
    boxes = "".join(cells)

    body_html = f"""\
<tr>
  <td style="padding:28px 36px 8px 36px;">
    <p style="margin:0 0 8px 0;font-size:14px;color:{INK};">{greeting}</p>
    <h1 style="margin:0 0 10px 0;font-size:22px;line-height:1.3;font-weight:400;color:{INK};">Your verification code</h1>
    <p style="margin:0 0 24px 0;font-size:15px;line-height:1.55;color:{MUTED};">
      Enter this code to continue. It expires in 10 minutes.
    </p>
    <table role="presentation" cellpadding="0" cellspacing="0"><tr>{boxes}</tr></table>
    <p style="margin:24px 0 0 0;font-size:13px;line-height:1.5;color:{MUTED};">
      If you didn&apos;t request this, you can ignore this email.
    </p>
  </td>
</tr>
"""
    html_body = _shell(f"Your Softune code is {otp}", body_html)
    text_body = (
        f"{greeting_text}\n\n"
        f"Your Softune verification code is: {otp}\n\n"
        "It expires in 10 minutes. If you didn't request this, ignore this email.\n\n"
        "Softune — softunebd.com"
    )
    return subject, html_body, text_body


def ticket_created_email(
    recipient_name: str | None, ticket_number_display: str, subject: str, message: str,
) -> tuple[str, str, str]:
    """Sent to the merchant right after they open a ticket."""
    greeting = f"Hi {html.escape(recipient_name)}," if recipient_name else "Hi,"
    greeting_text = f"Hi {recipient_name}," if recipient_name else "Hi,"
    email_subject = f"[{ticket_number_display}] We received your ticket"
    subject_safe = html.escape(subject)
    message_safe = html.escape(message)

    body_html = f"""\
<tr>
  <td style="padding:28px 36px 8px 36px;">
    <p style="margin:0 0 8px 0;font-size:14px;color:{INK};">{greeting}</p>
    <h1 style="margin:0 0 10px 0;font-size:22px;line-height:1.3;font-weight:400;color:{INK};">We&apos;ve got it</h1>
    <p style="margin:0 0 22px 0;font-size:15px;line-height:1.55;color:{MUTED};">
      A real person will reply to this email. Keep this thread — it stays on ticket {html.escape(ticket_number_display)}.
    </p>
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
           style="background-color:{SOFT};border-radius:{RADIUS};">
      <tr><td style="padding:16px 18px;">
        <p style="margin:0 0 4px 0;font-size:12px;color:{MUTED};">Ticket</p>
        <p style="margin:0 0 14px 0;font-size:16px;font-weight:400;color:{BRAND};">{html.escape(ticket_number_display)}</p>
        <p style="margin:0 0 4px 0;font-size:12px;color:{MUTED};">Subject</p>
        <p style="margin:0 0 14px 0;font-size:15px;font-weight:400;color:{INK};">{subject_safe}</p>
        <p style="margin:0 0 4px 0;font-size:12px;color:{MUTED};">Your message</p>
        <p style="margin:0;font-size:14px;line-height:1.55;color:{INK};white-space:pre-wrap;">{message_safe}</p>
      </td></tr>
    </table>
  </td>
</tr>
"""
    html_body = _shell(f"Ticket {ticket_number_display} received", body_html)
    text_body = (
        f"{greeting_text}\n\nWe've received your support request.\n\n"
        f"Ticket: {ticket_number_display}\nSubject: {subject}\n\nYour message:\n{message}\n\n"
        "Reply to this email any time — it stays on the same ticket.\n\n"
        f"Softune Support — {SUPPORT}"
    )
    return email_subject, html_body, text_body


def ticket_reply_email(
    recipient_name: str | None, ticket_number_display: str, subject: str, reply_message: str,
) -> tuple[str, str, str]:
    """Sent when a superadmin replies — one outbound message, not a chat bubble."""
    greeting = f"Hi {html.escape(recipient_name)}," if recipient_name else "Hi,"
    greeting_text = f"Hi {recipient_name}," if recipient_name else "Hi,"
    email_subject = f"[{ticket_number_display}] Re: {subject}"
    reply_safe = html.escape(reply_message)
    num_safe = html.escape(ticket_number_display)

    body_html = f"""\
<tr>
  <td style="padding:28px 36px 8px 36px;">
    <p style="margin:0 0 8px 0;font-size:14px;color:{INK};">{greeting}</p>
    <h1 style="margin:0 0 10px 0;font-size:22px;line-height:1.3;font-weight:400;color:{INK};">A reply on {num_safe}</h1>
    <p style="margin:0 0 22px 0;font-size:15px;line-height:1.55;color:{MUTED};">
      Here&apos;s an update on your request.
    </p>
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
           style="background-color:{SOFT};border-radius:{RADIUS};border-left:3px solid {BRAND};">
      <tr><td style="padding:18px 20px;">
        <p style="margin:0;font-size:15px;line-height:1.6;color:{INK};white-space:pre-wrap;">{reply_safe}</p>
      </td></tr>
    </table>
    <p style="margin:20px 0 0 0;font-size:13px;line-height:1.5;color:{MUTED};">
      Need anything else? Reply to this email — it stays on the same ticket.
    </p>
  </td>
</tr>
"""
    html_body = _shell(f"Update on ticket {ticket_number_display}", body_html)
    text_body = (
        f"{greeting_text}\n\nHere's an update on {ticket_number_display}:\n\n"
        f"{reply_message}\n\nNeed anything else? Reply to this email.\n\n"
        f"Softune Support — {SUPPORT}"
    )
    return email_subject, html_body, text_body


def contact_email(
    name: str, email: str, phone: str | None, message: str,
) -> tuple[str, str, str]:
    """Internal — landing Contact Us form to the support inbox. Skips the
    branded shell; plain is more useful here than on-brand."""
    subject = f"Contact form — {name}"
    rows = [("Name", name), ("Email", email), ("Phone", phone or "—"), ("Message", message)]
    text_body = "New contact form submission:\n\n" + "\n".join(f"{k}: {v}" for k, v in rows)
    html_rows = "".join(
        f'<tr><td style="padding:8px 0;color:{MUTED};font-size:12px;'
        f'width:88px;vertical-align:top;">{k}</td>'
        f'<td style="padding:8px 0;color:{INK};font-size:14px;white-space:pre-wrap;">{html.escape(str(v))}</td></tr>'
        for k, v in rows
    )
    html_body = f"""\
<!DOCTYPE html>
<html><body style="margin:0;padding:24px;background:{CANVAS};font-family:{FONT_STACK};">
<table role="presentation" width="520" cellpadding="0" cellspacing="0" style="max-width:100%;background:{SURFACE};border-radius:{RADIUS};padding:8px 24px;">
<tr><td style="padding:20px 0 8px 0;">
<p style="margin:0;font-size:12px;font-weight:400;color:{BRAND};">Inbound</p>
<h2 style="margin:6px 0 0 0;font-size:20px;color:{INK};">New contact form</h2>
</td></tr>
{html_rows}
</table>
</body></html>
"""
    return subject, html_body, text_body


def welcome_email(recipient_name: str | None = None) -> tuple[str, str, str]:
    """Sent once after POST /trial/complete — the store already exists."""
    greeting = f"Hi {html.escape(recipient_name)}," if recipient_name else "Hi,"
    greeting_text = f"Hi {recipient_name}," if recipient_name else "Hi,"
    subject = "Your Softune store is ready"

    items = [
        ("Theme editor", "Colors, fonts, and sections — live preview, no code."),
        ("Payments that Bangladesh uses", "COD, bKash, Nagad, and SSLCommerz."),
        ("Couriers, connected", "Steadfast, Pathao, RedX, and eCourier."),
        ("Three days, no card", "Full dashboard access. Upgrade when you're ready."),
    ]
    rows_html = "".join(
        f'<tr>'
        f'<td valign="top" width="28" style="padding:0 0 14px 0;font-size:14px;color:{MUTED};">{i}.</td>'
        f'<td valign="top" style="padding:0 0 14px 0;">'
        f'<p style="margin:0;font-size:15px;font-weight:400;color:{INK};">{title}</p>'
        f'<p style="margin:4px 0 0 0;font-size:14px;line-height:1.5;color:{MUTED};">{desc}</p>'
        f'</td></tr>'
        for i, (title, desc) in enumerate(items, start=1)
    )
    rows_text = "\n".join(f"{i}. {t} — {d}" for i, (t, d) in enumerate(items, start=1))

    body_html = f"""\
<tr>
  <td style="padding:28px 36px 8px 36px;">
    <p style="margin:0 0 8px 0;font-size:14px;color:{INK};">{greeting}</p>
    <h1 style="margin:0 0 10px 0;font-size:22px;line-height:1.3;font-weight:400;color:{INK};">Your store is live</h1>
    <p style="margin:0 0 24px 0;font-size:15px;line-height:1.55;color:{MUTED};">
      The 3-day trial is on. Open the dashboard, add a product, and publish when it looks right.
    </p>
    {_btn(DASHBOARD, "Open dashboard")}
  </td>
</tr>
<tr>
  <td style="padding:32px 36px 8px 36px;">
    <p style="margin:0 0 16px 0;font-size:14px;font-weight:400;color:{INK};">What you can do now</p>
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0">{rows_html}</table>
  </td>
</tr>
"""
    html_body = _shell("Your Softune store is ready — 3-day trial, no card", body_html)
    text_body = (
        f"{greeting_text}\n\n"
        "Your store is live. The 3-day trial is on — no credit card.\n\n"
        f"Open dashboard: {DASHBOARD}\n\n"
        f"{rows_text}\n\n"
        "Questions? Reply to this email.\n\n"
        "Softune — softunebd.com"
    )
    return subject, html_body, text_body


def demo_followup_email() -> tuple[str, str, str]:
    """One-click send from the superadmin demo-requests list. Pitch is
    'you've seen the demo — start a trial', not the welcome intro."""
    subject = "Build your own store — 3 days free, no card"
    signup = f"{SITE}/signup"
    bullets = [
        "Your shop name, theme, and products",
        "COD, bKash, Nagad, and SSLCommerz",
        "Three days, no credit card",
    ]
    bullets_html = "".join(
        f'<tr>'
        f'<td valign="top" width="18" style="padding:0 0 8px 0;font-size:14px;color:{MUTED};">•</td>'
        f'<td valign="top" style="padding:0 0 8px 0;font-size:15px;line-height:1.5;color:{INK};">{item}</td>'
        f'</tr>'
        for item in bullets
    )
    bullets_text = "\n".join(f"• {item}" for item in bullets)

    body_html = f"""\
<tr>
  <td style="padding:28px 36px 8px 36px;">
    <p style="margin:0 0 8px 0;font-size:14px;font-weight:400;color:{BRAND};">You tried the demo</p>
    <h1 style="margin:0 0 12px 0;font-size:22px;line-height:1.3;font-weight:400;color:{INK};">Now make it yours</h1>
    <p style="margin:0 0 18px 0;font-size:15px;line-height:1.55;color:{MUTED};">
      The demo is a shared, read-only shop. A trial is your shop for three days.
    </p>
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 22px 0;">{bullets_html}</table>
    {_btn(signup, "Start free trial")}
    <p style="margin:20px 0 0 0;font-size:13px;line-height:1.5;color:{MUTED};">
      About five minutes. Pick a theme, add your shop name, and you&apos;re in.
    </p>
  </td>
</tr>
"""
    html_body = _shell("Start your free 3-day trial — no card required", body_html)
    text_body = (
        "You tried the demo. Now make it yours.\n\n"
        "The demo is a shared, read-only shop. A trial is your shop for three days.\n\n"
        f"{bullets_text}\n\n"
        f"Start free trial: {signup}\n\n"
        "About five minutes. Pick a theme, add your shop name, and you're in.\n\n"
        "Softune — softunebd.com"
    )
    return subject, html_body, text_body
