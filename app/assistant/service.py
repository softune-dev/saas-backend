"""The database side of the assistant: build a store's index, read live facts.

Every query here is filtered by BOTH tenant_id and site_id of the site the
public host resolved to. The host is the only input a visitor controls, and it
is resolved to a site before anything else runs, so one store's assistant can
never see another store's products (tests/test_tenant_isolation.py).

The index is cached per host and dropped by `cache.invalidate_site`, which
every product / FAQ / shipping / event write already calls, so the knowledge
stays current with no job of its own: the next question after a change just
rebuilds it. Prices and stock are never in the index; `live_loader` reads
them from the database for the one or two products an answer is about.
"""

import logging
import re
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app import cache, events
from app.products import stock_status
from app.assistant.index import build_index
from app.assistant.schemas import AssistantSettings
from app.assistant.text import tokens
from app.models import AssistantQuestion, Category, Event, PaymentConnection, Product, Site

# Same helper the product pages use, so the assistant quotes the exact price
# (event discount included) that checkout will charge.
from app.api.public import _active_events_by_product


async def _site_data(db: AsyncSession, site: Site, settings: AssistantSettings) -> dict[str, Any]:
    scope = (Product.tenant_id == site.tenant_id, Product.site_id == site.id)
    products = (
        await db.execute(
            select(Product, Category.name)
            .outerjoin(Category, Product.category_id == Category.id)
            .where(*scope, Product.is_active)
            .order_by(Product.created_at.desc())
            .limit(500)
        )
    ).all()
    categories = (
        await db.execute(
            select(Category).where(Category.tenant_id == site.tenant_id, Category.site_id == site.id, Category.is_active)
        )
    ).scalars().all()
    active_events = (
        await db.execute(
            select(Event).where(Event.tenant_id == site.tenant_id, Event.site_id == site.id, Event.is_active)
        )
    ).scalars().all()
    payments = (
        await db.execute(
            select(PaymentConnection).where(
                PaymentConnection.tenant_id == site.tenant_id,
                PaymentConnection.site_id == site.id,
                PaymentConnection.status == "connected",
            )
        )
    ).scalars().all()

    return {
        "name": site.name,
        "business": site.business,
        "about": site.about,
        "faqs": site.faqs,
        "shipping": site.shipping,
        "payment_methods": [{"provider": p.provider, "label": p.label or p.provider} for p in payments],
        "categories": [{"id": c.id, "slug": c.slug, "name": c.name} for c in categories],
        "events": [{"name": e.name, "discount_percent": e.discount_percent} for e in active_events],
        "products": [
            {
                "id": p.id, "slug": p.slug, "name": p.name, "short": p.short_description or "",
                "category_name": cat_name,
                "delivery_charges": [
                    {"name": d.get("name", ""), "charge_cents": d.get("charge_cents") or 0}
                    for d in (p.delivery_charges or [])
                ],
            }
            for p, cat_name in products
        ],
        "qa": [{"q": x.q, "a": x.a} for x in settings.qa],
    }


async def load_index(db: AsyncSession, site: Site, host: str, settings: AssistantSettings) -> dict[str, Any]:
    key = cache.assistant_key(host)
    if cached := await cache.get_json(key):
        return cached
    index = build_index(await _site_data(db, site, settings))
    await cache.set_json(key, index)
    return index


def live_loader(db: AsyncSession, site: Site):
    """Callable that reads the CURRENT price / stock / options for products."""

    async def load(ids: list[str]) -> dict[str, dict[str, Any]]:
        if not ids:
            return {}
        uids = [uuid.UUID(i) for i in ids]
        rows = (
            await db.execute(
                select(Product).where(
                    Product.tenant_id == site.tenant_id, Product.site_id == site.id,
                    Product.is_active, Product.id.in_(uids),
                )
            )
        ).scalars().all()
        event_by_product = await _active_events_by_product(db, site.id, [p.id for p in rows])
        out: dict[str, dict[str, Any]] = {}
        for p in rows:
            ev = event_by_product.get(p.id)
            if ev is not None:
                price = events.round_discounted_cents(p.price_cents, ev.discount_percent)
                compare = p.price_cents
            else:
                price, compare = p.price_cents, p.compare_at_cents
            out[str(p.id)] = {
                "id": str(p.id), "slug": p.slug, "name": p.name,
                "image": next((i.get("url", "") for i in (p.images or []) if i.get("url")), ""),
                "price_cents": price, "compare_at_cents": compare,
                # Per-variant stock, same rule as the storefront (products.stock_status).
                "in_stock": stock_status(p)[0],
                "variants": [
                    {"type": v.get("type", ""), "values": [x.get("value", "") for x in v.get("values", [])]}
                    for v in ((p.attributes or {}).get("variants") or [])
                ],
                "delivery_charges": [
                    {"name": d.get("name", ""), "charge_cents": d.get("charge_cents") or 0}
                    for d in (p.delivery_charges or []) if d.get("name")
                ],
            }
        return out

    return load


def starters(index: dict[str, Any]) -> dict[str, list[str]]:
    """Opening suggestions drawn from what the store actually has, so a new
    store never offers a question it can't answer."""
    en, bn = [], []
    if index["products"]:
        name = index["products"][0]["name"]
        en.append(f"How much is {name}?")
        bn.append(f"{name} এর দাম কত?")
    if index["delivery"]["charges"] or index["delivery"]["free_over_cents"]:
        en.append("What is the delivery charge?")
        bn.append("ডেলিভারি চার্জ কত?")
    if index["payments"]:
        en.append("Which payments do you accept?")
        bn.append("কোন পেমেন্ট নেওয়া যায়?")
    if index["events"]:
        en.append("Any offers right now?")
        bn.append("এখন কোনো অফার আছে?")
    return {"en": en[:4], "bn": bn[:4]}


log = logging.getLogger(__name__)

MAX_OPEN_PER_SITE = 300
_DIGITS = re.compile(r"\d[\d\s\-]{4,}\d")


def clean_question(text: str) -> str:
    """What may be kept of a shopper's question: trimmed, and long digit runs
    (phone numbers, order numbers) masked, since the text is shown to the
    merchant and stored."""
    return _DIGITS.sub("•••", " ".join((text or "").split()))[:200]


def question_key(text: str) -> str:
    """Same question however it is phrased or ordered: sorted distinct words."""
    return " ".join(sorted(set(tokens(text))))[:200]


async def record_unanswered(db: AsyncSession, site: Site, question: str, lang: str) -> None:
    """Count a question the assistant could not answer, for the merchant's inbox.

    Best effort, like cache and queue helpers (CLAUDE.md rule 9): this runs on
    a customer's request and must never fail it, so every error is logged and
    swallowed. Bounded: a site stops collecting new distinct questions at
    MAX_OPEN_PER_SITE, so random text from an anonymous visitor can't grow the
    table without limit (repeats of an existing question still count).
    """
    try:
        key = question_key(question)
        text = clean_question(question)
        if not key or not text:
            return
        exists = (
            await db.execute(
                select(AssistantQuestion.id).where(
                    AssistantQuestion.tenant_id == site.tenant_id,
                    AssistantQuestion.site_id == site.id,
                    AssistantQuestion.question_key == key,
                )
            )
        ).first()
        if exists is None:
            open_count = (
                await db.execute(
                    select(func.count(AssistantQuestion.id)).where(
                        AssistantQuestion.site_id == site.id, AssistantQuestion.status == "open"
                    )
                )
            ).scalar_one()
            if open_count >= MAX_OPEN_PER_SITE:
                return
        stmt = pg_insert(AssistantQuestion).values(
            tenant_id=site.tenant_id, site_id=site.id, question=text, question_key=key,
            lang=lang if lang in ("en", "bn") else "en",
        )
        # Only OPEN questions keep counting: once answered or dismissed the
        # merchant has dealt with it, and a count would just be noise.
        stmt = stmt.on_conflict_do_update(
            constraint="uq_assistant_questions_site_key",
            set_={"times_asked": AssistantQuestion.times_asked + 1, "last_asked_at": func.now()},
            where=AssistantQuestion.status == "open",
        )
        await db.execute(stmt)
        await db.commit()
    except Exception as exc:  # noqa: BLE001 - must never fail the shopper's request
        log.warning("could not record unanswered assistant question: %s", exc)
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            pass
