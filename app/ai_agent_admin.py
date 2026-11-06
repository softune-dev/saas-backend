"""AI agent tools for the store's back office: domains, fraud protection,
courier automation, account, invoices and the Help Desk.

What stays OUT of chat on purpose, and why:
  * Courier and payment keys, passwords: everything said in chat is stored in
    ai_usage_logs, so a secret typed here would sit in a log.
  * Disconnecting a live domain: it takes the store offline at that address.
  * Paying or submitting a payment claim: that is money, and it needs a real
    transaction id checked by the merchant on the Billing page.

Support tickets go through the existing confirm card, because they email our
team and can't be taken back. Everything else records an Undo.
"""

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import ai_actions, ai_tools, cache, crud, domains, queue, vercel
from app.ai_agent_settings import _result, patch_column
from app.ai_agent_tools import _clip, _forget_tool_cache, _record, _site
from app.models import (
    FraudBlocklistEntry, FraudIpBlocklistEntry, HelpTicket, Invoice, Order, Site, Template, Tenant, User,
)
from app.schemas import FraudBlocklistEntryCreate, FraudIpBlocklistEntryCreate

FRAUD_RULES = ("hold_first_high_value", "flag_burst_orders", "block_blocklist", "device_pending_lock", "device_cooldown")
FRAUD_DECISIONS = ("cleared", "confirmed_fraud")  # schemas.FraudReviewIn
TENANT_FIELDS: dict[str, int] = {
    "legal_name": 200, "trade_name": 200, "business_type": 40, "trade_license": 100, "tin": 100, "billing_email": 200,
}
NOTIFICATIONS = ("orders", "low_stock", "billing", "marketing")
USER_FIELDS: dict[str, int] = {"full_name": 120, "phone": 32}
TICKET_CATEGORIES = ["Billing", "Technical", "Domain", "Shipping", "Account", "Other"]

_STR = {"type": "STRING"}
_BOOL = {"type": "BOOLEAN"}

DECLARATIONS: list[dict] = [
    {
        "name": "get_store_admin_info",
        "description": "Read one back-office area: settings (full SEO, contact, About), legal (policy text), domain (live status and DNS), fraud (flagged orders, blocklists), account, invoices, tickets.",
        "parameters": {"type": "OBJECT", "properties": {
            "section": {"type": "STRING", "enum": ["settings", "legal", "domain", "fraud", "account", "invoices", "tickets"]},
        }, "required": ["section"]},
    },
    {
        "name": "request_custom_domain",
        "description": "Start connecting a domain the merchant owns. The store keeps its current address until DNS works. Applied immediately; undo cancels the request.",
        "parameters": {"type": "OBJECT", "properties": {"domain": _STR}, "required": ["domain"]},
    },
    {
        "name": "update_fraud_rules",
        "description": "Turn fraud rules on or off and set thresholds. Applied immediately; undo restores it.",
        "parameters": {"type": "OBJECT", "properties": {
            "rules": {"type": "ARRAY", "items": {
                "type": "OBJECT",
                "properties": {
                    "rule": {"type": "STRING", "enum": list(FRAUD_RULES)},
                    "enabled": _BOOL,
                    "value": {"type": "INTEGER", "description": "Taka for high value, else minutes."},
                },
                "required": ["rule"],
            }},
        }, "required": ["rules"]},
    },
    {
        "name": "update_blocklist",
        "description": "Block or unblock a phone number or IP address. Applied immediately; undo reverses it.",
        "parameters": {"type": "OBJECT", "properties": {
            "action": {"type": "STRING", "enum": ["block", "unblock"]},
            "kind": {"type": "STRING", "enum": ["phone", "ip"]},
            "value": _STR, "note": _STR,
        }, "required": ["action", "kind", "value"]},
    },
    {
        "name": "review_suspicious_order",
        "description": "Clear a flagged order or confirm it as fraud. Applied immediately; undo puts it back in the review queue.",
        "parameters": {"type": "OBJECT", "properties": {
            "order_number": _STR, "decision": {"type": "STRING", "enum": list(FRAUD_DECISIONS)},
        }, "required": ["order_number", "decision"]},
    },
    {
        "name": "set_courier_auto_book",
        "description": "Turn automatic Steadfast booking of new orders on or off. Needs a connected Steadfast account. Applied immediately; undo restores it.",
        "parameters": {"type": "OBJECT", "properties": {"enabled": _BOOL}, "required": ["enabled"]},
    },
    {
        "name": "update_account",
        "description": "Change the merchant's profile name or phone, legal business details for invoices, or notification preferences. Applied immediately; undo restores it.",
        "parameters": {"type": "OBJECT", "properties": {
            **{k: _STR for k in USER_FIELDS}, **{k: _STR for k in TENANT_FIELDS},
            **{f"notify_{k}": _BOOL for k in NOTIFICATIONS},
        }},
    },
    {
        "name": "propose_support_ticket",
        "description": "Open a support ticket with the SoftuneBD team. Shows a confirm card; nothing is sent until the merchant confirms.",
        "parameters": {"type": "OBJECT", "properties": {
            "subject": _STR, "message": _STR,
            "category": {"type": "STRING", "enum": TICKET_CATEGORIES},
            "priority": {"type": "STRING", "enum": ["Low", "Medium", "High"]},
        }, "required": ["subject", "message"]},
    },
]

PROPOSAL_TOOLS = {"propose_support_ticket"}


def proposal_for(args: dict) -> dict:
    return {
        "type": "create_ticket", "subject": _clip(args.get("subject"), 200), "message": _clip(args.get("message"), 5000),
        "category": args.get("category") or "Other", "priority": args.get("priority") or "Medium",
    }


async def _tenant(db: AsyncSession, tenant_id: uuid.UUID) -> Tenant:
    return (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one()


async def is_demo(db: AsyncSession, tenant_id: uuid.UUID) -> bool:
    # The REST routes refuse demo accounts through block_demo_writes.
    return (await _tenant(db, tenant_id)).plan == "demo"


async def _user(db: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID | None) -> User | None:
    return (await db.execute(select(User).where(User.id == user_id, User.tenant_id == tenant_id))).scalar_one_or_none()


# --- reads -------------------------------------------------------------------

async def get_store_admin_info(
    db: AsyncSession, tenant_id: uuid.UUID, section: str, _user_id: uuid.UUID | None = None
) -> dict:
    site = await _site(db, tenant_id)
    if section == "settings":
        return {"seo": site.seo or {}, "contact": site.business or {}, "about": site.about or {},
                "shipping": site.shipping or {}, "courier_rules": site.courier_rules or {}}
    if section == "legal":
        return {"legal": site.legal or {}}
    if section == "domain":
        return await _domain_info(db, site)
    if section == "fraud":
        return await _fraud_info(db, site)
    if section == "account":
        tenant = await _tenant(db, tenant_id)
        user = await _user(db, tenant_id, _user_id)
        return {
            "profile": {"full_name": user.full_name, "email": user.email, "phone": user.phone} if user else None,
            "business": tenant.business or {}, "notifications": tenant.notifications, "plan": tenant.plan,
        }
    if section == "invoices":
        rows, total = await crud.list_scoped(db, Invoice, tenant_id, order_by=Invoice.issued_at.desc(), limit=12)
        return {"total": total, "invoices": [
            {"number": r.invoice_number, "plan": r.plan, "amount_taka": r.amount_cents / 100,
             "period": r.period_label, "issued": str(r.issued_at)[:10]} for r in rows
        ]}
    if section == "tickets":
        rows, total = await crud.list_scoped(db, HelpTicket, tenant_id, order_by=HelpTicket.created_at.desc(), limit=10)
        return {"total": total, "tickets": [
            {"number": f"TKT-{r.ticket_number:05d}", "subject": r.subject, "status": r.status, "priority": r.priority}
            for r in rows
        ]}
    return {"error": "Unknown section."}


async def _domain_info(db: AsyncSession, site: Site) -> dict:
    domain = site.pending_custom_domain or site.custom_domain
    out: dict[str, Any] = {
        "custom_domain": site.custom_domain, "pending_domain": site.pending_custom_domain,
        "dns_records": "Exact records are shown in Settings > Domains.",
    }
    if domain:
        project = (await db.execute(
            select(Template.vercel_project_id).where(Template.id == site.template_id)
        )).scalar_one_or_none()
        connected = await vercel.check_domain_connected(domain, project or "")
        out["connected"] = connected
        if site.pending_custom_domain and connected is True:
            out["promoted"] = await domains.promote(db, site)
    return out


async def _fraud_info(db: AsyncSession, site: Site) -> dict:
    orders, _ = await crud.list_scoped(
        db, Order, site.tenant_id, filters=[Order.site_id == site.id, Order.fraud_status == "flagged"],
        order_by=Order.created_at.desc(), limit=20,
    )
    phones, _ = await crud.list_scoped(db, FraudBlocklistEntry, site.tenant_id, filters=[FraudBlocklistEntry.site_id == site.id], limit=50)
    ips, _ = await crud.list_scoped(db, FraudIpBlocklistEntry, site.tenant_id, filters=[FraudIpBlocklistEntry.site_id == site.id], limit=50)
    return {
        "flagged_orders": [{"order_number": o.order_number, "total_taka": o.total_cents / 100, "reason": o.fraud_reason} for o in orders],
        "blocked_phones": [{"phone": p.phone, "note": p.note} for p in phones],
        "blocked_ips": [{"ip": i.ip_address, "note": i.note} for i in ips],
    }


# --- domain ------------------------------------------------------------------

async def _set_pending_domain(db: AsyncSession, site: Site, domain: str | None) -> None:
    """Same steps as PATCH /sites/{id}: a request never touches the live domain."""
    old = site.pending_custom_domain
    site.pending_custom_domain = domain
    site.pending_domain_requested_at = datetime.now(UTC) if domain else None
    await crud.save(db, site)
    await cache.invalidate_site(site.subdomain, site.custom_domain)
    await cache.invalidate_dashboard(str(site.id))
    if domain and site.status == "published":
        await queue.publish(queue.JOB_ATTACH_DOMAIN, {"site_id": str(site.id), "domain": domain})
    if old and old != domain and old != site.custom_domain:
        await queue.publish(queue.JOB_DETACH_DOMAIN, {"site_id": str(site.id), "domain": old})


async def request_custom_domain(db: AsyncSession, tenant_id: uuid.UUID, domain: str) -> dict:
    site = await _site(db, tenant_id)
    try:
        cleaned = domains.normalize_domain(domain)
    except ValueError as exc:
        return {"error": str(exc)}
    if not cleaned:
        return {"error": "Tell me which domain to connect."}
    if cleaned in (site.custom_domain, site.pending_custom_domain):
        return {"ok": True, "unchanged": True, "message": "That domain is already set on this store."}
    taken = (await db.execute(select(Site.id).where(Site.custom_domain == cleaned, Site.id != site.id))).first()
    if taken:
        return {"error": "That domain is already connected to another site."}
    before = site.pending_custom_domain
    await _set_pending_domain(db, site, cleaned)
    change = await _record(db, site, "request_custom_domain", f"Requested the domain {cleaned}",
                           {"type": "domain_request", "before": before, "after": cleaned})
    await _forget_tool_cache(tenant_id)
    return _result(change, next_step="The merchant must add the DNS records shown in Settings > Domains at their domain provider.")


# --- fraud -------------------------------------------------------------------

async def update_fraud_rules(db: AsyncSession, tenant_id: uuid.UUID, rules: list) -> dict:
    site = await _site(db, tenant_id)
    current = site.fraud_rules or {}
    patch: dict[str, dict] = {}
    for item in rules:
        rule = item.get("rule")
        if rule not in FRAUD_RULES:
            return {"error": "Unknown fraud rule.", "choices": list(FRAUD_RULES)}
        merged = dict(current.get(rule) or {})
        if item.get("enabled") is not None:
            merged["enabled"] = bool(item["enabled"])
        if item.get("value") is not None:
            if int(item["value"]) <= 0:
                return {"error": "A threshold must be above zero."}
            merged["value"] = int(item["value"])
        patch[rule] = merged
    if not patch:
        return {"error": "Nothing to change."}
    return await patch_column(db, tenant_id, "update_fraud_rules", "fraud_rules", patch, "Updated fraud protection rules")


def _block_model(kind: str) -> tuple[Any, str]:
    return (FraudIpBlocklistEntry, "ip_address") if kind == "ip" else (FraudBlocklistEntry, "phone")


async def _find_block(db: AsyncSession, site: Site, kind: str, value: str) -> Any:
    model, column = _block_model(kind)
    rows, _ = await crud.list_scoped(
        db, model, site.tenant_id, filters=[model.site_id == site.id, getattr(model, column) == value], limit=1
    )
    return rows[0] if rows else None


async def _add_block(db: AsyncSession, site: Site, kind: str, value: str, note: str) -> None:
    if kind == "ip":
        await crud.save(db, FraudIpBlocklistEntry(tenant_id=site.tenant_id, site_id=site.id, ip_address=value, note=note))
        await cache.invalidate_ip_blocks(site.subdomain, site.custom_domain)
    else:
        await crud.save(db, FraudBlocklistEntry(tenant_id=site.tenant_id, site_id=site.id, phone=value, note=note))


async def _remove_block(db: AsyncSession, site: Site, row: Any, kind: str) -> None:
    await crud.delete(db, row)
    if kind == "ip":
        await cache.invalidate_ip_blocks(site.subdomain, site.custom_domain)


async def update_blocklist(
    db: AsyncSession, tenant_id: uuid.UUID, action: str, kind: str, value: str, note: str = ""
) -> dict:
    site = await _site(db, tenant_id)
    try:
        if kind == "ip":
            value = FraudIpBlocklistEntryCreate(ip_address=value).ip_address
        else:
            value = FraudBlocklistEntryCreate(phone=str(value).strip()).phone
    except ValidationError:
        return {"error": f"That isn't a valid {'IP address' if kind == 'ip' else 'phone number'}."}
    note = _clip(note, 280)
    if action == "block":
        await _add_block(db, site, kind, value, note)
        undo_spec = {"type": "blocklist", "action": "unblock", "kind": kind, "value": value, "note": note}
        summary = f"Blocked {value}"
    elif action == "unblock":
        row = await _find_block(db, site, kind, value)
        if row is None:
            return {"error": f"{value} isn't on the blocklist."}
        note = row.note
        await _remove_block(db, site, row, kind)
        undo_spec = {"type": "blocklist", "action": "block", "kind": kind, "value": value, "note": note}
        summary = f"Unblocked {value}"
    else:
        return {"error": "action must be block or unblock"}
    change = await _record(db, site, "update_blocklist", summary, undo_spec)
    await _forget_tool_cache(tenant_id)
    return _result(change)


async def review_suspicious_order(db: AsyncSession, tenant_id: uuid.UUID, order_number: str, decision: str) -> dict:
    if decision not in FRAUD_DECISIONS:
        return {"error": "decision must be cleared or confirmed_fraud"}
    site = await _site(db, tenant_id)
    order = await ai_actions._find_order(db, site, None, order_number)
    if order.fraud_status != "flagged":
        return {"error": "This order isn't waiting for review."}
    summary = f"Marked order {order.order_number} as {decision.replace('_', ' ')}"
    order.fraud_status = decision
    await crud.save(db, order)
    change = await _record(db, site, "review_suspicious_order", summary,
                           {"type": "fraud_review", "order_id": str(order.id), "after": decision})
    await _forget_tool_cache(tenant_id)
    return _result(change)


# --- courier -----------------------------------------------------------------

async def set_courier_auto_book(db: AsyncSession, tenant_id: uuid.UUID, enabled: bool) -> dict:
    if enabled:
        connections = await ai_tools.execute_tool("list_courier_connections", {}, db, tenant_id)
        if "steadfast" not in json.dumps(connections, default=str).lower():
            return {"error": "Steadfast isn't connected yet. The merchant must add their keys on the Courier page."}
    return await patch_column(
        db, tenant_id, "set_courier_auto_book", "courier_rules",
        {"auto_book": {"enabled": bool(enabled), "provider": "steadfast"}},
        f"Turned automatic courier booking {'on' if enabled else 'off'}",
    )


# --- account -----------------------------------------------------------------

def _apply_account(tenant: Tenant, user: User | None, user_patch: dict, business: dict, notify: dict) -> None:
    for key, value in user_patch.items():
        setattr(user, key, value)
    if business:
        tenant.business = {**(tenant.business or {}), **business}
    if notify:
        stored = (tenant.settings or {}).get("notifications", {})
        tenant.settings = {**(tenant.settings or {}), "notifications": {**stored, **notify}}


async def update_account(db: AsyncSession, tenant_id: uuid.UUID, _user_id: uuid.UUID | None = None, **args: Any) -> dict:
    site = await _site(db, tenant_id)
    tenant = await _tenant(db, tenant_id)
    user = await _user(db, tenant_id, _user_id)
    user_patch = {k: _clip(args[k], n) for k, n in USER_FIELDS.items() if args.get(k) is not None}
    business_patch = {k: _clip(args[k], n) for k, n in TENANT_FIELDS.items() if args.get(k) is not None}
    notify_patch = {k: bool(args[f"notify_{k}"]) for k in NOTIFICATIONS if args.get(f"notify_{k}") is not None}
    if not (user_patch or business_patch or notify_patch):
        return {"error": "Nothing to change on the account."}
    if user_patch and user is None:
        return {"error": "Your profile can't be changed from here."}
    undo_spec = {
        "type": "account", "user_id": str(user.id) if user else None,
        "user": {k: getattr(user, k) for k in user_patch} if user else {},
        "business": {k: (tenant.business or {}).get(k) for k in business_patch},
        "notifications": {k: tenant.notifications[k] for k in notify_patch},
    }
    _apply_account(tenant, user, user_patch, business_patch, notify_patch)
    await db.commit()
    change = await _record(db, site, "update_account", "Updated account details", undo_spec)
    await _forget_tool_cache(tenant_id)
    return _result(change)


# --- undo --------------------------------------------------------------------

UNDO_KINDS = {"domain_request", "blocklist", "fraud_review", "account"}


async def undo(db: AsyncSession, site: Site, spec: dict) -> None:
    kind = spec["type"]
    if kind == "domain_request":
        if site.pending_custom_domain == spec.get("after"):
            await _set_pending_domain(db, site, spec.get("before"))
    elif kind == "blocklist":
        row = await _find_block(db, site, spec["kind"], spec["value"])
        if spec["action"] == "unblock" and row is not None:
            await _remove_block(db, site, row, spec["kind"])
        elif spec["action"] == "block" and row is None:
            await _add_block(db, site, spec["kind"], spec["value"], spec.get("note") or "")
    elif kind == "fraud_review":
        order = await crud.get_scoped(db, Order, site.tenant_id, uuid.UUID(spec["order_id"]))
        if order.fraud_status == spec.get("after"):
            order.fraud_status = "flagged"
            await crud.save(db, order)
    elif kind == "account":
        tenant = await _tenant(db, site.tenant_id)
        user_id = uuid.UUID(spec["user_id"]) if spec.get("user_id") else None
        user = await _user(db, site.tenant_id, user_id)
        _apply_account(tenant, user, (spec.get("user") or {}) if user else {},
                       spec.get("business") or {}, spec.get("notifications") or {})
        await db.commit()


HANDLERS = {
    "get_store_admin_info": get_store_admin_info,
    "request_custom_domain": request_custom_domain,
    "update_fraud_rules": update_fraud_rules,
    "update_blocklist": update_blocklist,
    "review_suspicious_order": review_suspicious_order,
    "set_courier_auto_book": set_courier_auto_book,
    "update_account": update_account,
}
