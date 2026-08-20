"""Customers — created implicitly from orders, never directly."""


async def _product(account, site, **kw) -> dict:
    body = {"name": "Widget", "price_cents": 1000, "stock": 10, **kw}
    return (await account.post(f"/sites/{site['id']}/products", json=body)).json()


async def _order(account, site, product, phone: str, **kw) -> dict:
    body = {
        "customer": {"name": "Buyer", "phone": phone},
        "items": [{"product_id": product["id"], "quantity": 1}],
        **kw,
    }
    return (await account.post(f"/sites/{site['id']}/orders", json=body)).json()


async def test_first_order_creates_a_customer(account, site):
    product = await _product(account, site)
    order = await _order(account, site, product, "01712345678")
    assert order["customer_id"] is not None

    listed = await account.get(f"/sites/{site['id']}/customers")
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["phone"] == "1712345678"


async def test_repeat_order_reuses_the_same_customer(account, site):
    product = await _product(account, site)
    first = await _order(account, site, product, "01712345678")
    second = await _order(account, site, product, "01712345678")
    assert first["customer_id"] == second["customer_id"]

    listed = await account.get(f"/sites/{site['id']}/customers")
    assert listed.json()["total"] == 1, "same phone number must not create two customer rows"


async def test_different_phone_formats_dedupe_to_one_customer(account, site):
    product = await _product(account, site)
    first = await _order(account, site, product, "01712345678")
    second = await _order(account, site, product, "+880 1712-345678")
    assert first["customer_id"] == second["customer_id"]


async def test_order_with_no_phone_has_no_customer(account, site):
    product = await _product(account, site)
    order = (
        await account.post(
            f"/sites/{site['id']}/orders",
            json={
                "customer": {"name": "No Phone"},
                "items": [{"product_id": product["id"], "quantity": 1}],
            },
        )
    ).json()
    assert order["customer_id"] is None


async def test_customer_detail_aggregates_linked_orders(account, site):
    product = await _product(account, site, price_cents=500)
    await _order(account, site, product, "01712345678")
    await _order(account, site, product, "01712345678")

    customer_id = (
        await account.get(f"/sites/{site['id']}/customers")
    ).json()["items"][0]["id"]

    detail = await account.get(f"/sites/{site['id']}/customers/{customer_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["order_count"] == 2
    assert body["total_spent_cents"] == 1000
    assert len(body["orders"]) == 2
    assert body["last_order_at"] is not None


async def test_customer_can_be_renamed(account, site):
    product = await _product(account, site)
    order = await _order(account, site, product, "01712345678")

    updated = await account.patch(
        f"/sites/{site['id']}/customers/{order['customer_id']}",
        json={"name": "Renamed Buyer", "email": "buyer@example.test"},
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Renamed Buyer"
    assert updated.json()["email"] == "buyer@example.test"
