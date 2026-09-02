"""Events — sale/promo campaigns with a real checkout discount."""

from app import recaptcha
from app.events import PLAN_EVENT_LIMIT, round_discounted_cents


async def _product(account, site, **kw) -> dict:
    body = {"name": "Widget", "price_cents": 1000, "stock": 10, **kw}
    return (await account.post(f"/sites/{site['id']}/products", json=body)).json()


# ---------------------------------------------------------------------------
#  CRUD
# ---------------------------------------------------------------------------


async def test_event_crud(account, site):
    product = await _product(account, site)

    created = await account.post(
        f"/sites/{site['id']}/events",
        json={"name": "Summer Sale", "discount_percent": 20, "product_ids": [product["id"]]},
    )
    assert created.status_code == 201
    event = created.json()
    assert event["slug"] == "summer-sale"
    assert event["product_ids"] == [product["id"]]
    assert event["product_count"] == 1
    assert event["is_active"] is False
    assert event["cta_label"] == "Shop now"

    updated = await account.patch(
        f"/sites/{site['id']}/events/{event['id']}", json={"discount_percent": 30}
    )
    assert updated.json()["discount_percent"] == 30
    assert updated.json()["slug"] == "summer-sale", "slug must be stable"

    listed = await account.get(f"/sites/{site['id']}/events")
    assert listed.json()["total"] == 1

    assert (
        await account.delete(f"/sites/{site['id']}/events/{event['id']}")
    ).status_code == 204
    assert (await account.get(f"/sites/{site['id']}/events")).json()["total"] == 0

    # Deleting the event must not delete the bound product.
    survived = await account.get(f"/sites/{site['id']}/products/{product['id']}")
    assert survived.status_code == 200


async def test_duplicate_event_slug_is_rejected(account, site):
    await account.post(
        f"/sites/{site['id']}/events", json={"name": "Same Name", "discount_percent": 10}
    )
    second = await account.post(
        f"/sites/{site['id']}/events", json={"name": "Same Name", "discount_percent": 10}
    )
    assert second.status_code == 409


async def test_discount_percent_bounds_are_rejected(account, site):
    too_low = await account.post(
        f"/sites/{site['id']}/events", json={"name": "A", "discount_percent": 0}
    )
    assert too_low.status_code == 422
    too_high = await account.post(
        f"/sites/{site['id']}/events", json={"name": "B", "discount_percent": 91}
    )
    assert too_high.status_code == 422


async def test_product_from_another_site_is_rejected(account, site, template_id):
    other_site = (
        await account.post(
            "/sites",
            json={"template_id": template_id, "name": "Other", "subdomain": "other-evt-site"},
        )
    ).json()
    other_product = await _product(account, other_site)

    response = await account.post(
        f"/sites/{site['id']}/events",
        json={"name": "Cross-site", "discount_percent": 10, "product_ids": [other_product["id"]]},
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
#  Plan limit
# ---------------------------------------------------------------------------


async def test_event_plan_limit_is_enforced(account, site):
    limit = PLAN_EVENT_LIMIT["starter"]  # conftest's account fixture registers plan="starter"
    for i in range(limit):
        response = await account.post(
            f"/sites/{site['id']}/events", json={"name": f"Event {i}", "discount_percent": 10}
        )
        assert response.status_code == 201

    over = await account.post(
        f"/sites/{site['id']}/events", json={"name": "One too many", "discount_percent": 10}
    )
    assert over.status_code == 403
    assert "limit" in over.json()["detail"].lower()


# ---------------------------------------------------------------------------
#  One active event per product
# ---------------------------------------------------------------------------


async def test_one_active_event_per_product(account, site):
    product = await _product(account, site)

    a = (
        await account.post(
            f"/sites/{site['id']}/events",
            json={
                "name": "Event A",
                "discount_percent": 10,
                "product_ids": [product["id"]],
                "is_active": True,
            },
        )
    ).json()
    assert a["is_active"] is True

    conflict = await account.post(
        f"/sites/{site['id']}/events",
        json={
            "name": "Event B",
            "discount_percent": 15,
            "product_ids": [product["id"]],
            "is_active": True,
        },
    )
    assert conflict.status_code == 409
    assert "widget" in conflict.json()["detail"].lower()

    # Creating it inactive is fine — no conflict until it's actually activated.
    b = (
        await account.post(
            f"/sites/{site['id']}/events",
            json={"name": "Event B", "discount_percent": 15, "product_ids": [product["id"]]},
        )
    ).json()
    assert b["is_active"] is False

    # Activating B while A is still active on the same product must fail...
    still_conflict = await account.patch(
        f"/sites/{site['id']}/events/{b['id']}", json={"is_active": True}
    )
    assert still_conflict.status_code == 409

    # ...but deactivating A first frees the product up.
    await account.patch(f"/sites/{site['id']}/events/{a['id']}", json={"is_active": False})
    now_ok = await account.patch(
        f"/sites/{site['id']}/events/{b['id']}", json={"is_active": True}
    )
    assert now_ok.status_code == 200
    assert now_ok.json()["is_active"] is True


# ---------------------------------------------------------------------------
#  Discount math
# ---------------------------------------------------------------------------


def test_round_discounted_cents_is_pure_integer_round_half_up():
    assert round_discounted_cents(1000, 25) == 750
    assert round_discounted_cents(999, 10) == 899  # 899.1 -> rounds down
    assert round_discounted_cents(995, 10) == 896  # 895.5 -> rounds up (half-up, not banker's)
    assert round_discounted_cents(100, 1) == 99
    assert round_discounted_cents(100, 90) == 10


async def test_checkout_applies_active_event_discount(account, site, monkeypatch):
    """The core requirement: an active event's discount is actually charged,
    not cosmetic — see app/api/public.py::create_public_order.

    RECAPTCHA_SECRET_KEY is a real, configured key in this environment (not
    empty, which is the only condition app/recaptcha.py auto-passes under),
    so an anonymous checkout call needs verify() mocked here — same
    pre-existing gap that already makes test_inquiries.py's public-endpoint
    test fail on its own, unrelated to this feature."""
    async def _fake_verify(*args, **kwargs):
        return recaptcha.VerifyResult(ok=True)

    monkeypatch.setattr(recaptcha, "verify", _fake_verify)
    product = await _product(account, site, price_cents=1000, stock=5)
    event = (
        await account.post(
            f"/sites/{site['id']}/events",
            json={
                "name": "Flash Sale",
                "discount_percent": 25,
                "product_ids": [product["id"]],
                "is_active": True,
            },
        )
    ).json()

    published = await account.post(f"/sites/{site['id']}/publish", json={})
    assert published.status_code == 200
    host = published.json()["subdomain"]

    order_response = await account.client.post(
        f"/public/site/{host}/orders",
        json={
            "customer": {"first_name": "Buyer", "phone": "01711111111"},
            "items": [{"product_id": product["id"], "quantity": 2}],
        },
    )
    assert order_response.status_code == 201, order_response.text
    order = order_response.json()
    item = order["items"][0]
    assert item["unit_price_cents"] == 750  # 1000 * (100-25)/100
    assert item["total_cents"] == 1500
    assert item["event_name"] == "Flash Sale"
    assert item["event_discount_percent"] == 25
    assert order["subtotal_cents"] == 1500

    # Deactivating the event stops it applying to new orders.
    await account.patch(f"/sites/{site['id']}/events/{event['id']}", json={"is_active": False})
    second_order = await account.client.post(
        f"/public/site/{host}/orders",
        json={
            "customer": {"first_name": "Buyer", "phone": "01711111111"},
            "items": [{"product_id": product["id"], "quantity": 1}],
        },
    )
    assert second_order.json()["items"][0]["unit_price_cents"] == 1000
    assert second_order.json()["items"][0]["event_name"] is None
