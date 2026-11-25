"""Things that keep running, on the Free plan: the public config, checkout
payments, background jobs and the cache after a plan change.

Every case uses a tenant of its own (deleted afterwards). Nothing here calls a
worker sweep or notify function: the sweep's SELECT is tested through
domains.pending_stmt, filtered to the test's tenants. Vercel, Meta, the cache
and the queue are faked.
"""

import dataclasses
import uuid
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app import plans
from app import audit, billing_actions, cache, domains, marketing, queue, vercel, worker
from app import courier_crypto
from app.api import superadmin
from app.assistant import service as assistant_service
from app.assistant.schemas import AssistantSettings
from app.db import SessionLocal
from app.models import Invoice, MarketingConnection, PaymentConnection, Site, Tenant, User
from app.schemas import SuperAdminTenantUpdate
from app.security import Principal

_ips = iter(range(1, 65000))

SEO = {
    "keywords": "saree, shop",
    "sitemap_enabled": True,
    "google_analytics": "G-TEST123",
    "google_search_console": "gsc-token",
    "facebook_pixel": "1234567890",
    "tiktok_pixel": "TT-TEST",
    "gtm_container_id": "GTM-TEST",
    "favicon": "https://cdn.test/f.png",
}


@pytest.fixture(autouse=True)
def queued(monkeypatch) -> list[tuple[str, dict]]:
    sent: list[tuple[str, dict]] = []

    async def spy(job, payload=None, *a, **k):
        sent.append((job, payload))
        return True

    monkeypatch.setattr(queue, "publish", spy)
    return sent


@pytest.fixture(autouse=True)
def store(monkeypatch) -> dict:
    """In-memory Redis stand-in: key -> (value, ttl)."""
    kept: dict = {}

    async def get_json(key):
        return kept[key][0] if key in kept else None

    async def set_json(key, value, ttl=None):
        kept[key] = (value, ttl)

    async def drop(*keys):
        for key in keys:
            kept.pop(key, None)

    def no_redis():
        raise ConnectionError("no redis in tests")

    monkeypatch.setattr(cache, "get_json", get_json)
    monkeypatch.setattr(cache, "set_json", set_json)
    monkeypatch.setattr(cache, "drop", drop)
    monkeypatch.setattr(cache, "client", no_redis)
    return kept


async def _seed(acct) -> None:
    """Publish the account's own site with every id and four connections."""
    async with SessionLocal() as db:
        site = await db.get(Site, uuid.UUID(acct.site_id))
        site.seo = dict(SEO)
        for provider, label in (("bkash", "bKash"), ("nagad", "Nagad"), ("cod", "Cash"), ("manual", "Send money")):
            db.add(
                PaymentConnection(
                    tenant_id=uuid.UUID(acct.tenant_id), site_id=site.id, provider=provider,
                    status="connected", label=label, config={"note": provider},
                )
            )
        await db.commit()
    res = await acct.post(f"/sites/{acct.site_id}/publish", json={})
    assert res.status_code == 200, res.text


async def _config(acct) -> dict:
    res = await acct.client.get(f"/public/site/{acct.subdomain}")
    assert res.status_code == 200, res.text
    return res.json()


# ---------------------------------------------------------------------------
#  Public config
# ---------------------------------------------------------------------------


async def test_starter_config_is_unchanged(account_factory):
    """Snapshot: every key and value a paying merchant's storefront got before
    this task (plus the ordering and badge keys added by the order-limit task)."""
    acct = await account_factory("starter")
    await _seed(acct)
    cfg = await _config(acct)

    assert set(cfg) == {"ordering", "badge", "site", "nav", "pages", "json_ld", "updated_at"}
    assert cfg["ordering"] == "checkout"
    assert cfg["badge"] is False
    site = cfg["site"]
    assert set(site) == {
        "id", "name", "template_key", "framework", "theme", "business", "about", "seo",
        "faqs", "legal", "free_delivery_over_cents", "payment_methods",
    }
    assert site["seo"] == SEO
    assert sorted(site["payment_methods"], key=lambda m: m["provider"]) == [
        {"provider": "bkash", "label": "bKash", "config": {"note": "bkash"}},
        {"provider": "cod", "label": "Cash", "config": {"note": "cod"}},
        {"provider": "manual", "label": "Send money", "config": {"note": "manual"}},
        {"provider": "nagad", "label": "Nagad", "config": {"note": "nagad"}},
    ]


async def test_free_config_hides_tracking_but_offers_every_payment_method(account_factory):
    acct = await account_factory("free")
    await _seed(acct)
    cfg = await _config(acct)

    seo = cfg["site"]["seo"]
    assert (seo["google_analytics"], seo["facebook_pixel"], seo["tiktok_pixel"], seo["gtm_container_id"]) == ("", "", "", "")
    # Not tracking: left alone.
    assert seo["keywords"] == SEO["keywords"]
    assert seo["google_search_console"] == SEO["google_search_console"]
    assert seo["favicon"] == SEO["favicon"]
    assert sorted(m["provider"] for m in cfg["site"]["payment_methods"]) == ["bkash", "cod", "manual", "nagad"]

    async with SessionLocal() as db:
        stored = (await db.get(Site, uuid.UUID(acct.site_id))).seo
        gateways = (
            await db.execute(
                select(func.count()).select_from(PaymentConnection).where(
                    PaymentConnection.tenant_id == uuid.UUID(acct.tenant_id),
                    PaymentConnection.provider.in_(["bkash", "nagad"]),
                )
            )
        ).scalar_one()
    assert stored["facebook_pixel"] == SEO["facebook_pixel"], "the stored ids are never touched"
    assert gateways == 2


# ---------------------------------------------------------------------------
#  Checkout and gateway payment
# ---------------------------------------------------------------------------


async def _product(acct) -> str:
    res = await acct.post(
        f"/sites/{acct.site_id}/products", json={"name": "Saree", "price_cents": 1000, "stock": 20}
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def _order(acct, product_id, **extra):
    return await acct.client.post(
        f"/public/site/{acct.subdomain}/orders",
        json={
            "customer": {"first_name": "Buyer", "phone": "01711111111"},
            "items": [{"product_id": product_id, "quantity": 1}],
            **extra,
        },
        headers={"X-Forwarded-For": f"198.51.100.{next(_ips) % 250 + 1}"},
    )


async def test_free_checkout_accepts_a_gateway(account_factory):
    acct = await account_factory("free")
    await _seed(acct)
    product = await _product(acct)

    placed = await _order(acct, product, payment_method="bkash")
    assert placed.status_code == 201, placed.text


def _without_gateways(monkeypatch):
    """Free as it would be if gateways were ever made paid-only again."""
    monkeypatch.setitem(plans.PLANS, "free", dataclasses.replace(plans.PLANS["free"], features=frozenset()))


async def test_a_plan_without_gateways_is_refused_at_checkout_and_at_payment(account_factory, monkeypatch):
    """All the gates still follow the one plan feature."""
    acct = await account_factory("free")
    await _seed(acct)
    product = await _product(acct)
    _without_gateways(monkeypatch)

    assert (await _order(acct, product, payment_method="bkash")).status_code == 400
    placed = await _order(acct, product, payment_method="cod")
    assert placed.status_code == 201, placed.text
    # The connection exists and is "connected", yet no gateway session may start.
    res = await acct.client.post(f"/public/site/{acct.subdomain}/orders/{placed.json()['order_number']}/pay/bkash")
    assert res.status_code == 400


async def test_free_checkout_accepts_manual_softune_pay(account_factory):
    acct = await account_factory("free")
    await _seed(acct)
    product = await _product(acct)
    res = await _order(acct, product, payment_method="manual", transaction_id="TRX12345AB")
    assert res.status_code == 201, res.text


# ---------------------------------------------------------------------------
#  Background jobs
# ---------------------------------------------------------------------------


async def _capi_job(acct, monkeypatch) -> list[dict]:
    sent: list[dict] = []

    async def fake_send(**kwargs):
        sent.append(kwargs)
        return True, None

    async def fake_record(*a, **k):
        return None

    monkeypatch.setattr(marketing, "send_purchase_event", fake_send)
    monkeypatch.setattr(worker, "_record_capi_outcome", fake_record)
    async with SessionLocal() as db:
        site = await db.get(Site, uuid.UUID(acct.site_id))
        site.seo = dict(SEO)
        db.add(
            MarketingConnection(
                tenant_id=site.tenant_id, site_id=site.id, provider="meta_capi", status="connected",
                access_token_encrypted=courier_crypto.encrypt("fake-token"), access_token_hint="oken",
            )
        )
        await db.commit()
    await worker.handle_send_meta_capi_event(
        {
            "site_id": acct.site_id, "event_id": "e1", "event_time": 1, "value": 10,
            "currency": "BDT", "order_number": "T-1",
        }
    )
    return sent


async def test_capi_job_sends_on_starter_but_not_on_free(account_factory, monkeypatch):
    starter = await account_factory("starter")
    assert len(await _capi_job(starter, monkeypatch)) == 1
    free = await account_factory("free")
    assert await _capi_job(free, monkeypatch) == []


async def test_attach_domain_job_custom_domain_needs_the_domain_feature(account_factory, monkeypatch):
    added: list[str] = []

    async def fake_add(host, project_id):
        added.append(host)
        return True

    monkeypatch.setattr(vercel, "add_domain_to_project", fake_add)
    free = await account_factory("free")
    async with SessionLocal() as db:
        site = await db.get(Site, uuid.UUID(free.site_id))
        site.pending_custom_domain = "shop.example.org"
        await db.commit()
        has_project = bool(site.template.vercel_project_id and site.template.framework == "nextjs")

    await worker.handle_attach_domain({"site_id": free.site_id, "domain": "shop.example.org"})
    assert added == []

    # A fresh publish queues the job with no domain: that is the free store's
    # own subdomain, which must still be attached (when its template has a project).
    await worker.handle_attach_domain({"site_id": free.site_id})
    assert added == ([f"{free.subdomain}.{worker.settings.site_base_domain}"] if has_project else [])


async def test_pending_domain_sweep_skips_free_tenants(account_factory):
    free = await account_factory("free")
    starter = await account_factory("starter")
    for acct in (free, starter):
        async with SessionLocal() as db:
            site = await db.get(Site, uuid.UUID(acct.site_id))
            site.pending_custom_domain = f"{acct.subdomain}.example.org"
            site.pending_domain_requested_at = datetime.now(UTC)
            await db.commit()
    mine = [uuid.UUID(free.tenant_id), uuid.UUID(starter.tenant_id)]
    async with SessionLocal() as db:
        found = (await db.execute(domains.pending_stmt().where(Site.tenant_id.in_(mine)))).scalars().all()
        still = await db.get(Site, uuid.UUID(free.site_id))
    assert [str(s.tenant_id) for s in found] == [starter.tenant_id]
    assert still.pending_custom_domain, "the request stays pending on Free"


async def test_assistant_offers_exactly_what_checkout_offers(account_factory, monkeypatch):
    acct = await account_factory("free")
    await _seed(acct)

    async def offered() -> list[str]:
        async with SessionLocal() as db:
            site = await db.get(Site, uuid.UUID(acct.site_id))
            data = await assistant_service._site_data(db, site, AssistantSettings())
        return sorted(p["provider"] for p in data["payment_methods"])

    assert await offered() == ["bkash", "cod", "manual", "nagad"]
    _without_gateways(monkeypatch)
    assert await offered() == ["cod", "manual"]


# ---------------------------------------------------------------------------
#  A plan change
# ---------------------------------------------------------------------------


async def _admin(email: str) -> Principal:
    async with SessionLocal() as db:
        user = (await db.execute(select(User).where(User.email == email))).scalar_one()
    return Principal(user_id=user.id, tenant_id=user.tenant_id, role=user.role, is_superadmin=True)


async def test_plan_change_drops_the_cache_and_queues_revalidation(account_factory, store, queued):
    acct = await account_factory("starter")
    await _seed(acct)
    await _config(acct)
    assert cache.site_key(acct.subdomain) in store, "the config was cached"
    queued.clear()

    await billing_actions.refresh_storefronts(uuid.UUID(acct.tenant_id))
    assert cache.site_key(acct.subdomain) not in store
    assert queued == [(queue.JOB_REVALIDATE_SITE, {"site_id": acct.site_id})]


async def test_the_helper_never_fails_a_plan_change(account_factory, monkeypatch):
    acct = await account_factory("starter")

    async def boom(*a, **k):
        raise RuntimeError("redis down")

    monkeypatch.setattr(cache, "invalidate_site", boom)
    await billing_actions.refresh_storefronts(uuid.UUID(acct.tenant_id))


async def _invoices(tenant_id: str) -> int:
    async with SessionLocal() as db:
        return (
            await db.execute(
                select(func.count()).select_from(Invoice).where(Invoice.tenant_id == uuid.UUID(tenant_id))
            )
        ).scalar_one()


async def test_superadmin_free_makes_no_invoice_and_no_renewal_and_refreshes(
    account_factory, monkeypatch, store, queued
):
    async def no_audit(*a, **k):
        return None

    monkeypatch.setattr(audit, "record", no_audit)
    acct = await account_factory("starter")
    await _seed(acct)
    await _config(acct)
    admin = await _admin(acct.email)
    async with SessionLocal() as db:
        tenant = await db.get(Tenant, uuid.UUID(acct.tenant_id))
        tenant.plan_renews_at = datetime.now(UTC)
        tenant.plan_overdue_since = datetime.now(UTC)
        await db.commit()
    before = await _invoices(acct.tenant_id)
    queued.clear()

    async with SessionLocal() as db:
        out = await superadmin.update_tenant(
            uuid.UUID(acct.tenant_id), SuperAdminTenantUpdate(plan="free"), admin, db
        )
    assert out["plan"] == "free"
    assert out["plan_renews_at"] is None
    assert await _invoices(acct.tenant_id) == before
    async with SessionLocal() as db:
        assert (await db.get(Tenant, uuid.UUID(acct.tenant_id))).plan_overdue_since is None
    assert cache.site_key(acct.subdomain) not in store, "the cached starter config is gone"
    assert (queue.JOB_REVALIDATE_SITE, {"site_id": acct.site_id}) in queued
    assert not any(job == queue.JOB_GENERATE_INVOICE_PDF for job, _ in queued)

    # The next read is the Free config, not the cached paid one.
    assert (await _config(acct))["site"]["seo"]["facebook_pixel"] == ""

    async with SessionLocal() as db:
        with pytest.raises(HTTPException) as exc:
            await superadmin.confirm_plan_renewal(uuid.UUID(acct.tenant_id), admin, db)
    assert exc.value.status_code == 400
    assert await _invoices(acct.tenant_id) == before
