"""WhatsApp Business Platform (Meta Cloud API) — sends FROM Softunebd's own
business number TO a merchant, e.g. the post-signup welcome message.

NOT a per-tenant integration — contrast Site.business.whatsapp, which is a
merchant's own contact number shown to THEIR storefront visitors. This
module only ever sends as the platform itself.

TEMPLATES, NOT FREE TEXT: any business-initiated message (one the recipient
didn't message first) must use a template pre-approved by Meta — there is
no way around this, it's a WhatsApp Business Platform rule, not a choice
made here. Approval is usually fast (minutes to a day) but happens in Meta
Business Manager, not through this API. WHATSAPP_WELCOME_TEMPLATE names the
exact template this module sends; if you rename or recreate the template in
Meta's UI, update that constant to match.
"""

import logging
import re

import httpx

from app.config import settings

log = logging.getLogger(__name__)

GRAPH_API_VERSION = "v21.0"

# Must match a template's exact name in Meta Business Manager -> WhatsApp
# Manager -> Message Templates. Suggested template (category: UTILITY,
# language: English (en) or Bengali (bn) depending on what you submit):
#
#   Body: "Hi {{1}}! Welcome to Softunebd. If you need any help setting up
#   your store or run into an issue, just reply here — we're happy to help."
#
# {{1}} is the merchant's first name, the only variable this module fills in.
WHATSAPP_WELCOME_TEMPLATE = "softunebd_welcome"

_TIMEOUT_SECONDS = 8.0
_BD_MOBILE_RE = re.compile(r"^01[3-9]\d{8}$")


def to_whatsapp_number(raw: str) -> str | None:
    """Normalizes a Bangladeshi mobile number to WhatsApp's expected E.164-
    without-plus format ("8801XXXXXXXXX"). Returns None for anything that
    doesn't look like a real BD mobile number — same acceptance rule as
    app/api/public.py's _validate_bd_phone, so a number good enough for
    checkout is good enough here, and vice versa.
    """
    digits = re.sub(r"\D", "", raw or "")
    if digits.startswith("880"):
        digits = "0" + digits[3:]
    if not _BD_MOBILE_RE.match(digits):
        return None
    return "880" + digits[1:]


async def send_template_message(
    *, to: str, template_name: str, language_code: str = "en", body_params: list[str] | None = None,
) -> tuple[bool, str | None]:
    """Sends one approved template message. Returns (ok, error_message) —
    never raises. A misconfigured token or an unapproved template name is
    exactly as recoverable as any other integration failure in this
    codebase (courier/payment/AI): log it, tell the caller, move on.
    """
    if not settings.whatsapp_phone_number_id or not settings.whatsapp_access_token:
        return False, "WhatsApp isn't configured (WHATSAPP_PHONE_NUMBER_ID / WHATSAPP_ACCESS_TOKEN)."

    url = (
        f"https://graph.facebook.com/{GRAPH_API_VERSION}/"
        f"{settings.whatsapp_phone_number_id}/messages"
    )
    payload: dict = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": language_code},
        },
    }
    if body_params:
        payload["template"]["components"] = [
            {
                "type": "body",
                "parameters": [{"type": "text", "text": p} for p in body_params],
            }
        ]

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            res = await client.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {settings.whatsapp_access_token}"},
            )
    except httpx.HTTPError as exc:
        return False, f"Couldn't reach WhatsApp: {exc}"

    if res.status_code == 200:
        return True, None

    # Meta's error body is genuinely useful here (e.g. "template not found",
    # "re-engagement message" if 24h window issues ever apply to a future
    # non-template send) — surface it instead of just the status code.
    try:
        detail = res.json().get("error", {}).get("message", res.text)
    except Exception:  # noqa: BLE001 — response body parsing, not our own logic
        detail = res.text
    return False, f"WhatsApp rejected the message: {detail}"
