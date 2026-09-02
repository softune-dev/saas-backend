"""THE MOST IMPORTANT TEST FILE IN THE PROJECT.

Everything else here is a feature. This is the security boundary: proof that
tenant A cannot see, edit or delete tenant B's data.

A failure in this file is not "a broken test" — it is a data breach in waiting.
Treat it exactly as seriously as you would a production incident, and never
comment one of these out to get a green run.

The pattern in every test: tenant A creates something, tenant B tries to touch it
using the real id, and must get 404. Not 403 — 404. A 403 would confirm the id
exists, which lets an attacker map out other tenants' records even without
reading them.
"""

import uuid

import pytest

from tests.conftest import Account


async def _make_site(acct: Account, template_id: str) -> dict:
    response = await acct.post(
        "/sites",
        json={
            "template_id": template_id,
            "name": "A's site",
            "subdomain": f"iso-{uuid.uuid4().hex[:10]}",
        },
    )
    assert response.status_code == 201
    return response.json()


# ---------------------------------------------------------------------------
#  Sites
# ---------------------------------------------------------------------------


async def test_other_tenant_cannot_read_site(two_accounts, template_id):
    a, b = two_accounts
    site = await _make_site(a, template_id)

    assert (await a.get(f"/sites/{site['id']}")).status_code == 200
    assert (await b.get(f"/sites/{site['id']}")).status_code == 404


async def test_other_tenant_cannot_update_site(two_accounts, template_id):
    a, b = two_accounts
    site = await _make_site(a, template_id)

    response = await b.patch(f"/sites/{site['id']}", json={"name": "hijacked"})
    assert response.status_code == 404

    # And confirm nothing actually changed.
    after = await a.get(f"/sites/{site['id']}")
    assert after.json()["name"] == "A's site"


async def test_other_tenant_cannot_delete_site(two_accounts, template_id):
    a, b = two_accounts
    site = await _make_site(a, template_id)

    assert (await b.delete(f"/sites/{site['id']}")).status_code == 404
    assert (await a.get(f"/sites/{site['id']}")).status_code == 200  # still there


async def test_site_list_only_shows_own_sites(two_accounts, template_id):
    a, b = two_accounts
    site = await _make_site(a, template_id)

    # Every account is provisioned with one site at creation time (see
    # crud.create_tenant_owner_and_site) — b's list is never empty, the
    # real assertion is that it never contains a's site.
    b_list = await b.get("/sites")
    ids = [s["id"] for s in b_list.json()["items"]]
    assert site["id"] not in ids
    assert b_list.json()["total"] == 1


# ---------------------------------------------------------------------------
#  Pages — nested resources are where authorisation holes usually hide
# ---------------------------------------------------------------------------


async def test_other_tenant_cannot_list_pages(two_accounts, template_id):
    a, b = two_accounts
    site = await _make_site(a, template_id)
    assert (await b.get(f"/sites/{site['id']}/pages")).status_code == 404


async def test_other_tenant_cannot_create_page_on_your_site(two_accounts, template_id):
    """The classic nested-route hole: valid token, someone else's parent id."""
    a, b = two_accounts
    site = await _make_site(a, template_id)

    response = await b.post(
        f"/sites/{site['id']}/pages",
        json={"slug": "injected", "title": "Injected", "blocks": []},
    )
    assert response.status_code == 404

    pages = await a.get(f"/sites/{site['id']}/pages")
    assert "injected" not in [p["slug"] for p in pages.json()]


async def test_other_tenant_cannot_edit_page(two_accounts, template_id):
    a, b = two_accounts
    site = await _make_site(a, template_id)
    pages = (await a.get(f"/sites/{site['id']}/pages")).json()
    assert pages, "template seed should have created at least one page"
    page_id = pages[0]["id"]

    response = await b.patch(
        f"/sites/{site['id']}/pages/{page_id}", json={"title": "hijacked"}
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
#  Commerce
# ---------------------------------------------------------------------------


async def test_other_tenant_cannot_see_products(two_accounts, template_id):
    a, b = two_accounts
    site = await _make_site(a, template_id)

    created = await a.post(
        f"/sites/{site['id']}/products", json={"name": "Secret Widget", "price_cents": 999}
    )
    assert created.status_code == 201
    product_id = created.json()["id"]

    assert (await b.get(f"/sites/{site['id']}/products/{product_id}")).status_code == 404
    assert (await b.get(f"/sites/{site['id']}/products")).status_code == 404


async def test_other_tenant_cannot_see_events(two_accounts, template_id):
    a, b = two_accounts
    site = await _make_site(a, template_id)

    created = await a.post(
        f"/sites/{site['id']}/events", json={"name": "Secret Sale", "discount_percent": 20}
    )
    assert created.status_code == 201
    event_id = created.json()["id"]

    assert (await b.get(f"/sites/{site['id']}/events/{event_id}")).status_code == 404
    assert (await b.get(f"/sites/{site['id']}/events")).status_code == 404
    assert (
        await b.patch(f"/sites/{site['id']}/events/{event_id}", json={"discount_percent": 50})
    ).status_code == 404
    assert (await b.delete(f"/sites/{site['id']}/events/{event_id}")).status_code == 404


async def test_other_tenant_cannot_see_orders(two_accounts, template_id):
    a, b = two_accounts
    site = await _make_site(a, template_id)

    product = (
        await a.post(
            f"/sites/{site['id']}/products",
            json={"name": "Thing", "price_cents": 500, "stock": 10},
        )
    ).json()
    order = (
        await a.post(
            f"/sites/{site['id']}/orders",
            json={
                "customer": {"email": "buyer@example.test"},
                "items": [{"product_id": product["id"], "quantity": 1}],
            },
        )
    ).json()

    assert (await b.get(f"/sites/{site['id']}/orders/{order['id']}")).status_code == 404


async def test_other_tenant_cannot_see_customers(two_accounts, template_id):
    a, b = two_accounts
    site = await _make_site(a, template_id)

    product = (
        await a.post(
            f"/sites/{site['id']}/products",
            json={"name": "Thing", "price_cents": 500, "stock": 10},
        )
    ).json()
    await a.post(
        f"/sites/{site['id']}/orders",
        json={
            "customer": {"name": "Buyer", "phone": "01712345678"},
            "items": [{"product_id": product["id"], "quantity": 1}],
        },
    )
    customer_id = (await a.get(f"/sites/{site['id']}/customers")).json()["items"][0]["id"]

    assert (await b.get(f"/sites/{site['id']}/customers/{customer_id}")).status_code == 404
    assert (await b.get(f"/sites/{site['id']}/customers")).status_code == 404
    assert (
        await b.patch(f"/sites/{site['id']}/customers/{customer_id}", json={"name": "hijacked"})
    ).status_code == 404


async def test_other_tenant_cannot_see_inquiries(client, two_accounts, template_id):
    a, b = two_accounts
    site = await _make_site(a, template_id)
    await a.post(f"/sites/{site['id']}/publish")

    submitted = await client.post(
        f"/public/site/{site['subdomain']}/contact",
        json={"data": {"name": "Visitor", "email": "v@example.test", "message": "Hi"}},
    )
    assert submitted.status_code == 201

    assert (await b.get(f"/sites/{site['id']}/inquiries")).status_code == 404


async def test_cannot_attach_another_sites_category(two_accounts, template_id):
    """Cross-site references must be rejected even within scoping rules."""
    a, b = two_accounts
    a_site = await _make_site(a, template_id)
    b_site = await _make_site(b, template_id)

    a_category = (
        await a.post(f"/sites/{a_site['id']}/categories", json={"name": "A Category"})
    ).json()

    # B owns b_site, so it passes the ownership check — but the category is A's.
    response = await b.post(
        f"/sites/{b_site['id']}/products",
        json={"name": "X", "price_cents": 100, "category_id": a_category["id"]},
    )
    assert response.status_code in (400, 404)


# ---------------------------------------------------------------------------
#  Tokens
# ---------------------------------------------------------------------------


async def test_no_token_is_rejected(client, template_id):
    assert (await client.get("/sites")).status_code == 401


async def test_garbage_token_is_rejected(client):
    response = await client.get(
        "/sites", headers={"Authorization": "Bearer not.a.real.token"}
    )
    assert response.status_code == 401


async def test_refresh_token_cannot_be_used_as_access_token(client, account: Account):
    """A refresh token lives for weeks. If it were accepted on normal endpoints,
    a leaked one would grant long-term access instead of a single exchange."""
    login = await client.post(
        "/auth/login", json={"email": account.email, "password": "test-password-123"}
    )
    refresh = login.json()["refresh_token"]

    response = await client.get("/sites", headers={"Authorization": f"Bearer {refresh}"})
    assert response.status_code == 401


@pytest.mark.parametrize(
    "path",
    ["/sites", "/sites/00000000-0000-0000-0000-000000000000/pages"],
)
async def test_protected_paths_require_auth(client, path):
    assert (await client.get(path)).status_code == 401
