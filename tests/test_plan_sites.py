"""Free-plan gates on /sites and /pages: store count, SEO, custom domain, theme.

A free tenant already owns one site (account.site_id), because a free tenant
cannot create a second. Vercel, the queue and the cache are faked: nothing here
may reach an outside service (the test database is the production one).
"""

import asyncio
import uuid

import pytest
from sqlalchemy import select, update

from app import domains, vercel
from app.api import sites as sites_api
from app.config import settings
from app.db import SessionLocal
from app.models import Site
from app.schemas import SiteUpdate
from app.security import Principal


@pytest.fixture(autouse=True)
def _no_outside_world(monkeypatch):
    async def noop(*a, **kw):
        return None

    from app import cache, edge_config, queue

    monkeypatch.setattr(queue, "publish", noop)
    monkeypatch.setattr(cache, "invalidate_site", noop)
    monkeypatch.setattr(cache, "drop", noop)
    monkeypatch.setattr(cache, "invalidate_dashboard", noop)
    monkeypatch.setattr(edge_config, "sync_ip_blocks", noop)

    async def connected(domain, project_id):
        return True

    monkeypatch.setattr(vercel, "check_domain_connected", connected)


def _locked(response, feature: str, needed: str = "starter") -> None:
    assert response.status_code == 403, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "plan_locked"
    assert detail["feature"] == feature
    assert detail["needed_plan"] == needed
    assert detail["message"]


async def _site(account) -> dict:
    return (await account.get(f"/sites/{account.site_id}")).json()


# ---------------------------------------------------------------------------
#  PATCH /sites
# ---------------------------------------------------------------------------


async def test_free_cannot_change_seo_fraud_or_domain(account_factory):
    free = await account_factory("free")
    url = f"/sites/{free.site_id}"
    _locked(await free.patch(url, json={"seo": {"meta_description": "hello"}}), "seo")
    _locked(await free.patch(url, json={"fraud_rules": {"block_repeat": True}}), "fraud")
    _locked(await free.patch(url, json={"custom_domain": "shop.example.com"}), "domain")
    # Nothing was written by the refused requests.
    site = await _site(free)
    assert site["seo"] == {"noindex": True}
    assert site["pending_custom_domain"] is None


async def test_free_can_save_everything_that_is_not_locked(account_factory):
    free = await account_factory("free")
    url = f"/sites/{free.site_id}"
    for body in (
        {"custom_domain": None},
        {"theme": {"primaryColor": "#112233"}},
        {"business": {"phone": "01700000000"}},
        {"about": {"title": "Us"}},
        {"faqs": [{"question": "Q?", "answer": "A."}]},
        {"legal": {"privacy": "text"}},
        {"shipping": {"note": "x"}},
        {"assistant": {"enabled": False}},
        {"name": "Renamed"},
    ):
        response = await free.patch(url, json=body)
        assert response.status_code == 200, (body, response.text)


async def test_free_may_resend_locked_fields_unchanged(account_factory):
    """Whole-object saves must not break on Free: the onboarding SEO step sends
    an empty seo, and a settings form may echo the stored values back."""
    free = await account_factory("free")
    url = f"/sites/{free.site_id}"
    stored = await _site(free)

    same = await free.patch(
        url, json={"seo": stored["seo"], "fraud_rules": stored["fraud_rules"], "custom_domain": None}
    )
    assert same.status_code == 200, same.text

    # Empty / padded seo says nothing new, and must not wipe seo.noindex.
    for seo in ({}, {"meta_description": "", "keywords": None}):
        response = await free.patch(url, json={"seo": seo})
        assert response.status_code == 200, response.text
        assert response.json()["seo"] == {"noindex": True}
    assert (await free.patch(url, json={"fraud_rules": {}})).status_code == 200


async def test_free_may_remove_a_domain_but_not_request_a_new_one(account_factory):
    free = await account_factory("free")
    async with SessionLocal() as db:
        await db.execute(
            update(Site).where(Site.id == free.site_id).values(custom_domain="old.example.com")
        )
        await db.commit()
    url = f"/sites/{free.site_id}"
    # Re-sending the current domain is not a change.
    assert (await free.patch(url, json={"custom_domain": "old.example.com"})).status_code == 200
    _locked(await free.patch(url, json={"custom_domain": "new.example.com"}), "domain")
    cleared = await free.patch(url, json={"custom_domain": None})
    assert cleared.status_code == 200
    assert cleared.json()["custom_domain"] is None


async def test_starter_can_change_seo_fraud_and_domain(account_factory):
    starter = await account_factory("starter")
    url = f"/sites/{starter.site_id}"
    assert (await starter.patch(url, json={"seo": {"meta_description": "hello"}})).status_code == 200
    assert (await starter.patch(url, json={"fraud_rules": {"a": 1}})).status_code == 200
    domain = await starter.patch(url, json={"custom_domain": "shop.example.com"})
    assert domain.status_code == 200
    assert domain.json()["pending_custom_domain"] == "shop.example.com"


async def test_another_tenants_site_is_404_not_plan_locked(account_factory):
    free = await account_factory("free")
    other = await account_factory("starter")
    response = await free.patch(f"/sites/{other.site_id}", json={"seo": {"meta_description": "x"}})
    assert response.status_code == 404


async def test_direct_update_site_call_is_not_gated(account_factory):
    """ai_setup.apply_site calls update_site() itself to save the AI's SEO
    description; that server-side write must work on Free."""
    free = await account_factory("free")
    user = Principal(user_id=uuid.uuid4(), tenant_id=uuid.UUID(free.tenant_id), role="owner")
    async with SessionLocal() as db:
        out = await sites_api.update_site(
            uuid.UUID(free.site_id), SiteUpdate(seo={"meta_description": "from ai"}), user, db
        )
        assert out.seo["meta_description"] == "from ai"
    assert (await _site(free))["seo"]["meta_description"] == "from ai"


# ---------------------------------------------------------------------------
#  Pages
# ---------------------------------------------------------------------------


async def test_free_page_seo_is_locked_but_pages_are_not(account_factory):
    free = await account_factory("free")
    base = f"/sites/{free.site_id}/pages"
    _locked(
        await free.post(base, json={"slug": "a", "title": "A", "seo": {"title": "t"}}), "seo"
    )
    assert (await free.post(base, json={"slug": "b", "title": "B"})).status_code == 201
    made = await free.post(base, json={"slug": "c", "title": "C", "seo": {}})
    assert made.status_code == 201
    page_id = made.json()["id"]

    _locked(await free.patch(f"{base}/{page_id}", json={"seo": {"title": "t"}}), "seo")
    # Unchanged seo, and ordinary edits, still save.
    assert (await free.patch(f"{base}/{page_id}", json={"seo": {}, "title": "C2"})).status_code == 200
    assert (await free.patch(f"{base}/{page_id}", json={"title": "C3"})).status_code == 200


async def test_starter_page_seo_is_allowed(account_factory):
    starter = await account_factory("starter")
    base = f"/sites/{starter.site_id}/pages"
    made = await starter.post(base, json={"slug": "a", "title": "A", "seo": {"title": "t"}})
    assert made.status_code == 201
    done = await starter.patch(f"{base}/{made.json()['id']}", json={"seo": {"title": "u"}})
    assert done.status_code == 200


async def test_page_seo_on_another_tenants_site_is_404(account_factory):
    free = await account_factory("free")
    other = await account_factory("starter")
    response = await free.post(
        f"/sites/{other.site_id}/pages", json={"slug": "a", "title": "A", "seo": {"title": "t"}}
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
#  Theme
# ---------------------------------------------------------------------------


async def _other_template(account) -> str:
    templates = (await account.get("/templates")).json()
    current = (await _site(account))["template_id"]
    others = [t["id"] for t in templates if t["id"] != current]
    if not others:
        pytest.skip("needs two active templates")
    return others[0]


async def test_switch_theme_is_locked_on_free_and_works_on_starter(account_factory):
    free = await account_factory("free")
    target = await _other_template(free)
    _locked(await free.post(f"/sites/{free.site_id}/switch-theme", json={"template_id": target}), "theme_switch")

    starter = await account_factory("starter")
    done = await starter.post(
        f"/sites/{starter.site_id}/switch-theme", json={"template_id": target}
    )
    assert done.status_code == 200, done.text
    assert done.json()["template_id"] == target


# ---------------------------------------------------------------------------
#  Custom domain never goes live on Free
# ---------------------------------------------------------------------------


async def _set_pending(account, domain: str) -> None:
    async with SessionLocal() as db:
        await db.execute(
            update(Site).where(Site.id == account.site_id).values(pending_custom_domain=domain)
        )
        await db.commit()


async def test_domain_status_never_promotes_on_free(account_factory):
    free = await account_factory("free")
    await _set_pending(free, "held.example.com")
    response = await free.get(f"/sites/{free.site_id}/domain-status")
    assert response.status_code == 200
    assert response.json()["pending"] is True
    site = await _site(free)
    assert site["custom_domain"] is None
    assert site["pending_custom_domain"] == "held.example.com"


async def test_domain_status_promotes_on_starter(account_factory):
    starter = await account_factory("starter")
    await _set_pending(starter, "live.example.com")
    response = await starter.get(f"/sites/{starter.site_id}/domain-status")
    assert response.json()["pending"] is False
    assert (await _site(starter))["custom_domain"] == "live.example.com"


async def test_promote_refuses_on_free_directly(account_factory):
    """promote() is also what the worker sweep calls, so it carries the check."""
    free = await account_factory("free")
    await _set_pending(free, "swept.example.com")
    async with SessionLocal() as db:
        site = (await db.execute(select(Site).where(Site.id == free.site_id))).scalar_one()
        assert await domains.promote(db, site) is False
    site = await _site(free)
    assert site["custom_domain"] is None
    assert site["pending_custom_domain"] == "swept.example.com"


# ---------------------------------------------------------------------------
#  Store count
# ---------------------------------------------------------------------------


def _new_site(template_id: str, subdomain: str | None = None) -> dict:
    return {
        "template_id": template_id,
        "name": "Another",
        "subdomain": subdomain or f"plan-{uuid.uuid4().hex[:10]}",
    }


async def test_free_cannot_create_a_second_store_flag_off_or_on(account_factory, template_id, monkeypatch):
    free = await account_factory("free")
    for enabled in (False, True):
        monkeypatch.setattr(settings, "free_plan_enabled", enabled)
        response = await free.post("/sites", json=_new_site(template_id))
        assert response.status_code == 403, response.text
        assert isinstance(response.json()["detail"], str)  # a limit, not a feature lock


async def test_duplicate_subdomain_is_409_before_the_limit(account_factory, template_id):
    free = await account_factory("free")
    response = await free.post("/sites", json=_new_site(template_id, free.subdomain))
    assert response.status_code == 409
    assert "taken" in response.json()["detail"].lower()


async def test_paid_plans_are_unlimited_until_the_rollout_flag(account_factory, template_id, monkeypatch):
    monkeypatch.setattr(settings, "free_plan_enabled", False)
    starter = await account_factory("starter")
    assert (await starter.post("/sites", json=_new_site(template_id))).status_code == 201
    assert (await starter.post("/sites", json=_new_site(template_id))).status_code == 201


async def test_store_limit_follows_the_plan_table_once_enabled(account_factory, template_id, monkeypatch):
    monkeypatch.setattr(settings, "free_plan_enabled", True)
    starter = await account_factory("starter")
    assert (await starter.post("/sites", json=_new_site(template_id))).status_code == 403

    business = await account_factory("business")  # 2 stores, owns 1
    assert (await business.post("/sites", json=_new_site(template_id))).status_code == 201
    assert (await business.post("/sites", json=_new_site(template_id))).status_code == 403


async def test_concurrent_creates_cannot_both_take_the_last_slot(account_factory, template_id, monkeypatch):
    monkeypatch.setattr(settings, "free_plan_enabled", True)
    business = await account_factory("business")
    results = await asyncio.gather(
        business.post("/sites", json=_new_site(template_id)),
        business.post("/sites", json=_new_site(template_id)),
    )
    assert sorted(r.status_code for r in results) == [201, 403]
    listing = (await business.get("/sites")).json()
    assert listing["total"] == 2


async def test_free_cannot_delete_its_only_store(account_factory):
    free = await account_factory("free")
    response = await free.delete(f"/sites/{free.site_id}")
    assert response.status_code == 403
    assert (await free.get(f"/sites/{free.site_id}")).status_code == 200


async def test_paid_tenant_can_still_delete_a_store(account_factory, template_id):
    # Business: the paid plan with room for a second store.
    paid = await account_factory("business")
    extra = (await paid.post("/sites", json=_new_site(template_id))).json()
    assert (await paid.delete(f"/sites/{extra['id']}")).status_code == 204
