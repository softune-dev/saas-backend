"""Softune Pay: turn a wallet's "you received money" SMS into a paid order.

Flow: the merchant's phone (the Softune Pay app) forwards each payment SMS to
POST /pay/sms (app/api/pay.py). We parse it here, store it in pay_sms, and if a
pending *manual* order on that store has the same transaction ID and the same
total, mark the order paid. Shoppers often pay before they press "Place order",
so checkout calls match_new_order() to look for an SMS that already arrived.

What is trusted, and what isn't:
- The sender must be the wallet's official sender ID. A friend texting the
  merchant "You have received Tk 5000 ... TrxID X" from a normal number is
  ignored. (The app filters too; this is the server's own check.)
- Amount and transaction ID come from the SMS, never from the app's own fields.
- A transaction ID can confirm one order, once: pay_sms is unique per
  (site, provider, trx_id), so a repeated or replayed SMS can't pay twice.
- A matching ID with a different amount is never auto-confirmed; it's stored as
  "mismatch" for the merchant to look at (the shopper may have underpaid).

Same parsing spirit as app/api/public.py's bkash_sms_webhook (which confirms
Softune's own billing payments), extended to Nagad and Rocket.
"""

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import cache, crud, queue
from app.models import Order, PaySms

log = logging.getLogger(__name__)

# Official sender IDs, lowercased. Operators show these as the SMS "from".
# Rocket (Dutch-Bangla) sends from its short code.
SENDERS: dict[str, set[str]] = {
    "bkash": {"bkash"},
    "nagad": {"nagad"},
    "rocket": {"16216", "rocket"},
}


@dataclass(frozen=True)
class ParsedSms:
    provider: str
    trx_id: str
    amount_cents: int
    sender_number: str | None


def provider_for_sender(sender: str) -> str | None:
    s = (sender or "").strip().lower()
    for provider, names in SENDERS.items():
        if s in names:
            return provider
    return None


def _cents(text: str) -> int | None:
    try:
        return int((Decimal(text.replace(",", "")) * 100).to_integral_value())
    except (InvalidOperation, ValueError):
        return None


# Money-in wording only. "sent", "Cash Out", "Payment ... to" are money leaving
# the merchant's wallet and must never confirm an order.
_PATTERNS: dict[str, dict[str, re.Pattern[str]]] = {
    # "You have received Tk 1,450.00 from 01712344521. Fee Tk 0.00. Balance Tk
    #  12,830.50. TrxID BKD7H2K9QX at 11/11/2026 14:02"
    "bkash": {
        "incoming": re.compile(r"\byou have received\b|\breceived\s+tk\b", re.I),
        "amount": re.compile(r"received\s+Tk\.?\s*([\d,]+(?:\.\d{1,2})?)", re.I),
        "trx": re.compile(r"\bTrxID\s*:?\s*([A-Z0-9]{6,20})\b", re.I),
        "sender": re.compile(r"\bfrom\s+(01\d{9})\b", re.I),
    },
    # "Money Received. Amount: Tk 890.00 Sender: 01811230917 Ref: N/A
    #  TxnID: 71KQ8ZP3 Balance: Tk 4,210.00 11/11/2026 13:55"
    "nagad": {
        "incoming": re.compile(r"\bmoney received\b|\bcash in received\b", re.I),
        "amount": re.compile(r"\bAmount\s*:?\s*Tk\.?\s*([\d,]+(?:\.\d{1,2})?)", re.I),
        "trx": re.compile(r"\bTxnID\s*:?\s*([A-Z0-9]{6,20})\b", re.I),
        "sender": re.compile(r"\bSender\s*:?\s*(01\d{9})\b", re.I),
    },
    # "Tk600.00 received from A/C:016XXXX7702 Fee:Tk0, Your A/C Balance:
    #  Tk1,980.00 TxnId:2219044871 Date:11-NOV-26 01:21:10 pm"
    "rocket": {
        "incoming": re.compile(r"\breceived from\b", re.I),
        "amount": re.compile(r"\bTk\.?\s*([\d,]+(?:\.\d{1,2})?)\s+received\b", re.I),
        "trx": re.compile(r"\bTxnId\s*:?\s*([A-Z0-9]{6,20})\b", re.I),
        "sender": re.compile(r"\bA/C\s*:?\s*([0-9X]{11,13})\b", re.I),
    },
}


def parse_sms(sender: str, body: str) -> ParsedSms | None:
    """None when this isn't a money-in SMS from an official wallet sender
    (balance checks, OTPs, promos and outgoing transfers all land here)."""
    provider = provider_for_sender(sender)
    if provider is None:
        return None
    p = _PATTERNS[provider]
    if not p["incoming"].search(body):
        return None
    amount = p["amount"].search(body)
    trx = p["trx"].search(body)
    if not amount or not trx:
        return None
    cents = _cents(amount.group(1))
    if cents is None or cents <= 0:
        return None
    sender_match = p["sender"].search(body)
    return ParsedSms(provider, trx.group(1).upper(), cents, sender_match.group(1) if sender_match else None)


def mask_number(number: str | None) -> str | None:
    """01712344521 -> 017••••4521. Shown in the app; the full number never is."""
    if not number or len(number) < 8:
        return number
    return f"{number[:3]}••••{number[-4:]}"


async def _mark_paid(db: AsyncSession, order: Order, sms: PaySms) -> None:
    """Same end state as a gateway payment (app/api/public.py's
    _finalize_gateway_payment): status paid, the evidence on the order's meta,
    dashboard cache dropped and the merchant notified."""
    now = datetime.now(UTC)
    order.status = "paid"
    order.meta = {
        **(order.meta or {}),
        "paid_via": "softune_pay",
        "softune_pay_provider": sms.provider,
        "softune_pay_trx_id": sms.trx_id,
        "softune_pay_confirmed_at": now.isoformat(),
    }
    sms.status = "matched"
    sms.order_id = order.id
    sms.matched_at = now
    await crud.save(db, order)
    await crud.save(db, sms)
    # Helpers in cache.py/queue.py log and swallow (CLAUDE.md rule 9).
    await cache.invalidate_dashboard(str(order.site_id))
    await queue.publish(
        queue.JOB_SEND_ORDER_NOTIFICATIONS,
        {
            "tenant_id": str(order.tenant_id),
            "site_id": str(order.site_id),
            "type": "order_created",
            "title": f"Payment confirmed: {order.order_number}",
            "body": f"{sms.provider.capitalize()} ৳{order.total_cents / 100:,.0f} matched automatically by Softune Pay.",
            "link": f"/orders?highlight={order.id}",
            "send_push": True,
        },
    )


async def match_sms(db: AsyncSession, sms: PaySms) -> None:
    """Look for the pending manual order this SMS pays, and settle it.
    Leaves the SMS 'unmatched' if there's none yet (checkout may still come)."""
    order = (
        await db.execute(
            select(Order).where(
                Order.site_id == sms.site_id,
                Order.tenant_id == sms.tenant_id,
                Order.status == "pending",
                func.upper(Order.meta["transaction_id"].astext) == sms.trx_id.upper(),
            )
            .order_by(Order.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if order is None:
        return
    if order.total_cents != sms.amount_cents:
        # Never auto-confirm a different amount: the shopper may have sent less.
        sms.status = "mismatch"
        sms.order_id = order.id
        await crud.save(db, sms)
        return
    await _mark_paid(db, order, sms)


async def match_new_order(db: AsyncSession, order: Order) -> None:
    """Called right after checkout saves a manual order: the money may already
    have arrived. Best-effort; a failure here must never fail the order."""
    trx = str((order.meta or {}).get("transaction_id") or "").strip()
    if not trx or order.status != "pending":
        return
    try:
        sms = (
            await db.execute(
                select(PaySms).where(
                    PaySms.site_id == order.site_id,
                    PaySms.tenant_id == order.tenant_id,
                    PaySms.status == "unmatched",
                    PaySms.trx_id == trx,
                )
            )
        ).scalar_one_or_none()
        if sms is None:
            return
        if sms.amount_cents != order.total_cents:
            sms.status = "mismatch"
            sms.order_id = order.id
            await crud.save(db, sms)
            return
        await _mark_paid(db, order, sms)
    except Exception as exc:  # noqa: BLE001 - see docstring
        log.warning("softune pay: checkout match failed for order %s: %s", order.id, exc)
