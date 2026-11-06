"""Undo for changes the AI agent made (see app/ai_agent_tools.py for why the
agent applies edits immediately). Each AiAgentChange row stores exactly what
the change overwrote; this puts it back. Kept separate from the tools so the
write path and the reverse path can each be read on their own.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app import ai_agent_admin, ai_agent_settings, cache, crud
from app.ai_agent_tools import _forget_tool_cache, _template_key, _write_theme
from app.models import AiAgentChange, Category, Event, Product, Site

async def undo(db: AsyncSession, tenant_id: uuid.UUID, change_id: uuid.UUID) -> dict:
    # get_scoped: another tenant's change id is a 404, never a 403 (rule 3).
    change = await crud.get_scoped(db, AiAgentChange, tenant_id, change_id)
    if change.undone_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "That change was already undone.")
    site = await crud.get_scoped(db, Site, tenant_id, change.site_id)
    spec = change.undo or {}
    kind = spec.get("type")
    out: dict[str, Any] = {"summary": change.summary}

    if kind == "theme":
        theme = site.theme or {}
        # Only restore keys still holding what the agent wrote: if the
        # merchant edited one by hand since, their newer edit wins.
        restore = {
            k: v for k, v in (spec.get("before") or {}).items()
            if theme.get(k) == (spec.get("after") or {}).get(k)
        }
        if restore:
            await _write_theme(db, site, restore)
        out.update(theme=site.theme, template_key=await _template_key(db, site), skipped=len(spec.get("before") or {}) - len(restore))
    elif kind in ("delete_product", "delete_category", "delete_event"):
        model, key = {
            "delete_product": (Product, "product_id"),
            "delete_category": (Category, "category_id"),
            "delete_event": (Event, "event_id"),
        }[kind]
        try:
            row = await crud.get_scoped(db, model, tenant_id, uuid.UUID(spec[key]))
        except HTTPException:
            row = None  # already deleted by hand — nothing left to undo
        if row is not None:
            await db.delete(row)
            await db.commit()
        await cache.invalidate_site(site.subdomain, site.custom_domain)
        await cache.invalidate_dashboard(str(site.id))
    elif kind == "restore_category":
        row = await crud.get_scoped(db, Category, tenant_id, uuid.UUID(spec["category_id"]))
        for field, value in (spec.get("before") or {}).items():
            setattr(row, field, value)
        await crud.save(db, row)
        await cache.invalidate_site(site.subdomain, site.custom_domain)
        await cache.invalidate_dashboard(str(site.id))
    elif kind == "restore_product":
        row = await crud.get_scoped(db, Product, tenant_id, uuid.UUID(spec["product_id"]))
        for field, value in (spec.get("before") or {}).items():
            if field == "category_id":
                value = uuid.UUID(value) if value else None
            setattr(row, field, value)
        if "images" in (spec.get("before") or {}):
            flag_modified(row, "images")
        await crud.save(db, row)
        await cache.invalidate_site(site.subdomain, site.custom_domain)
        await cache.invalidate_dashboard(str(site.id))
    elif kind == "site_json":
        out["skipped"] = await ai_agent_settings.undo_site_json(db, site, spec)
    elif kind in ai_agent_admin.UNDO_KINDS:
        await ai_agent_admin.undo(db, site, spec)
    else:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "This change can't be undone.")

    change = await crud.get_scoped(db, AiAgentChange, tenant_id, change_id)
    change.undone_at = datetime.now(UTC)
    await db.commit()
    await _forget_tool_cache(tenant_id)
    return out
