"""Categories, products, orders — the CRUD surface."""


# ---------------------------------------------------------------------------
#  Categories
# ---------------------------------------------------------------------------


async def test_category_crud(account, site):
    created = await account.post(
        f"/sites/{site['id']}/categories", json={"name": "Hot Drinks"}
    )
    assert created.status_code == 201
    category = created.json()
    assert category["slug"] == "hot-drinks", "slug should be auto-derived from the name"

    updated = await account.patch(
        f"/sites/{site['id']}/categories/{category['id']}", json={"name": "Beverages"}
    )
    assert updated.json()["name"] == "Beverages"
    assert updated.json()["slug"] == "hot-drinks", "slug must be stable — URLs depend on it"

    listed = await account.get(f"/sites/{site['id']}/categories")
    assert len(listed.json()) == 1

    assert (
        await account.delete(f"/sites/{site['id']}/categories/{category['id']}")
    ).status_code == 204
    assert len((await account.get(f"/sites/{site['id']}/categories")).json()) == 0


async def test_category_cannot_be_its_own_parent(account, site):
    category = (
        await account.post(f"/sites/{site['id']}/categories", json={"name": "Loop"})
    ).json()
    response = await account.patch(
        f"/sites/{site['id']}/categories/{category['id']}",
        json={"parent_id": category["id"]},
    )
    assert response.status_code == 400


async def test_duplicate_category_slug_is_rejected(account, site):
    await account.post(f"/sites/{site['id']}/categories", json={"name": "Same Name"})
    second = await account.post(
        f"/sites/{site['id']}/categories", json={"name": "Same Name"}
    )
    assert second.status_code == 409


async def test_deleting_category_orphans_products_but_keeps_them(account, site):
    """ON DELETE SET NULL — deleting a category must not delete the products."""
    category = (
        await account.post(f"/sites/{site['id']}/categories", json={"name": "Temp"})
    ).json()
    product = (
        await account.post(
            f"/sites/{site['id']}/products",
            json={"name": "Survivor", "price_cents": 100, "category_id": category["id"]},
        )
    ).json()

    await account.delete(f"/sites/{site['id']}/categories/{category['id']}")

    after = await account.get(f"/sites/{site['id']}/products/{product['id']}")
    assert after.status_code == 200, "product should survive its category's deletion"
    assert after.json()["category_id"] is None


# ---------------------------------------------------------------------------
#  Products
# ---------------------------------------------------------------------------


async def test_product_crud(account, site):
    created = await account.post(
        f"/sites/{site['id']}/products",
        json={"name": "Flat White", "price_cents": 450, "stock": 20, "sku": "FW-01"},
    )
    assert created.status_code == 201
    product = created.json()
    assert product["slug"] == "flat-white"
    assert product["currency"] == "USD"

    updated = await account.patch(
        f"/sites/{site['id']}/products/{product['id']}", json={"price_cents": 495}
    )
    assert updated.json()["price_cents"] == 495
    assert updated.json()["stock"] == 20, "untouched fields must not be reset"

    assert (
        await account.delete(f"/sites/{site['id']}/products/{product['id']}")
    ).status_code == 204


async def test_negative_price_is_rejected(account, site):
    response = await account.post(
        f"/sites/{site['id']}/products", json={"name": "Free money", "price_cents": -100}
    )
    assert response.status_code == 422


async def test_duplicate_sku_is_rejected(account, site):
    body = {"name": "A", "price_cents": 100, "sku": "DUP-1"}
    assert (await account.post(f"/sites/{site['id']}/products", json=body)).status_code == 201
    second = await account.post(
        f"/sites/{site['id']}/products", json={**body, "name": "B", "slug": "b"}
    )
    assert second.status_code == 409
    assert "sku" in second.json()["detail"].lower()


async def test_product_search_and_filter(account, site):
    for name in ["Espresso", "Cappuccino", "Iced Tea"]:
        await account.post(
            f"/sites/{site['id']}/products", json={"name": name, "price_cents": 300}
        )

    # Substring search — this is the query the trigram GIN index accelerates.
    hits = await account.get(f"/sites/{site['id']}/products", params={"q": "ccino"})
    assert hits.json()["total"] == 1
    assert hits.json()["items"][0]["name"] == "Cappuccino"

    empty = await account.get(f"/sites/{site['id']}/products", params={"q": "zzzz"})
    assert empty.json()["total"] == 0


async def test_pagination_envelope(account, site):
    for i in range(5):
        await account.post(
            f"/sites/{site['id']}/products",
            json={"name": f"Item {i}", "price_cents": 100 + i},
        )

    page = await account.get(
        f"/sites/{site['id']}/products", params={"limit": 2, "offset": 0}
    )
    body = page.json()
    assert body["total"] == 5
    assert len(body["items"]) == 2
    assert body["limit"] == 2 and body["offset"] == 0


# ---------------------------------------------------------------------------
#  Orders
# ---------------------------------------------------------------------------


async def _product(account, site, **kw) -> dict:
    body = {"name": "Widget", "price_cents": 1000, "stock": 10, **kw}
    return (await account.post(f"/sites/{site['id']}/products", json=body)).json()


async def test_order_totals_are_computed_server_side(account, site):
    """The client sends ids and quantities only. Prices come from the database."""
    product = await _product(account, site, price_cents=250)

    response = await account.post(
        f"/sites/{site['id']}/orders",
        json={
            "customer": {"name": "Buyer", "email": "buyer@example.test"},
            "items": [{"product_id": product["id"], "quantity": 4}],
            "shipping_cents": 500,
            "tax_cents": 100,
        },
    )
    assert response.status_code == 201
    order = response.json()

    assert order["subtotal_cents"] == 1000       # 250 * 4
    assert order["total_cents"] == 1600          # + 500 shipping + 100 tax
    assert order["order_number"].startswith("ORD-")
    assert len(order["items"]) == 1
    assert order["items"][0]["unit_price_cents"] == 250


async def test_order_snapshots_survive_product_deletion(account, site):
    """The whole reason order_items stores snapshots — history must not rot."""
    product = await _product(account, site, name="Doomed Product", price_cents=777)
    order = (
        await account.post(
            f"/sites/{site['id']}/orders",
            json={"items": [{"product_id": product["id"], "quantity": 1}]},
        )
    ).json()

    await account.delete(f"/sites/{site['id']}/products/{product['id']}")

    after = await account.get(f"/sites/{site['id']}/orders/{order['id']}")
    assert after.status_code == 200
    item = after.json()["items"][0]
    assert item["name_snapshot"] == "Doomed Product"
    assert item["unit_price_cents"] == 777
    assert item["product_id"] is None, "FK is SET NULL, the snapshot carries the data"


async def test_stock_is_decremented(account, site):
    product = await _product(account, site, stock=10)
    await account.post(
        f"/sites/{site['id']}/orders",
        json={"items": [{"product_id": product["id"], "quantity": 3}]},
    )
    after = await account.get(f"/sites/{site['id']}/products/{product['id']}")
    assert after.json()["stock"] == 7


async def test_overselling_is_blocked(account, site):
    product = await _product(account, site, stock=2)
    response = await account.post(
        f"/sites/{site['id']}/orders",
        json={"items": [{"product_id": product["id"], "quantity": 5}]},
    )
    assert response.status_code == 409
    assert "only 2 left" in response.json()["detail"].lower()

    # And nothing was deducted — the whole transaction rolled back.
    after = await account.get(f"/sites/{site['id']}/products/{product['id']}")
    assert after.json()["stock"] == 2


async def test_untracked_stock_can_oversell(account, site):
    """Services and digital goods have no inventory to run out of."""
    product = await _product(account, site, stock=0, track_stock=False)
    response = await account.post(
        f"/sites/{site['id']}/orders",
        json={"items": [{"product_id": product["id"], "quantity": 99}]},
    )
    assert response.status_code == 201


async def test_inactive_product_cannot_be_ordered(account, site):
    product = await _product(account, site)
    await account.patch(
        f"/sites/{site['id']}/products/{product['id']}", json={"is_active": False}
    )
    response = await account.post(
        f"/sites/{site['id']}/orders",
        json={"items": [{"product_id": product["id"], "quantity": 1}]},
    )
    assert response.status_code == 400


async def test_order_status_transitions(account, site):
    product = await _product(account, site)
    order = (
        await account.post(
            f"/sites/{site['id']}/orders",
            json={"items": [{"product_id": product["id"], "quantity": 1}]},
        )
    ).json()
    assert order["status"] == "pending"

    updated = await account.patch(
        f"/sites/{site['id']}/orders/{order['id']}", json={"status": "paid"}
    )
    assert updated.json()["status"] == "paid"

    invalid = await account.patch(
        f"/sites/{site['id']}/orders/{order['id']}", json={"status": "teleported"}
    )
    assert invalid.status_code == 422


async def test_order_totals_are_immutable(account, site):
    """OrderUpdate has no total fields, so a client cannot rewrite history."""
    product = await _product(account, site, price_cents=1000)
    order = (
        await account.post(
            f"/sites/{site['id']}/orders",
            json={"items": [{"product_id": product["id"], "quantity": 1}]},
        )
    ).json()

    await account.patch(
        f"/sites/{site['id']}/orders/{order['id']}", json={"total_cents": 1},
    )
    after = await account.get(f"/sites/{site['id']}/orders/{order['id']}")
    assert after.json()["total_cents"] == 1000


async def test_order_with_unknown_product_is_rejected(account, site):
    response = await account.post(
        f"/sites/{site['id']}/orders",
        json={"items": [
            {"product_id": "00000000-0000-0000-0000-000000000000", "quantity": 1}
        ]},
    )
    assert response.status_code == 400


async def test_empty_order_is_rejected(account, site):
    response = await account.post(f"/sites/{site['id']}/orders", json={"items": []})
    assert response.status_code == 422


async def test_orders_filter_by_status(account, site):
    product = await _product(account, site, stock=100)
    for _ in range(3):
        await account.post(
            f"/sites/{site['id']}/orders",
            json={"items": [{"product_id": product["id"], "quantity": 1}]},
        )
    orders = (await account.get(f"/sites/{site['id']}/orders")).json()["items"]
    await account.patch(
        f"/sites/{site['id']}/orders/{orders[0]['id']}", json={"status": "paid"}
    )

    paid = await account.get(f"/sites/{site['id']}/orders", params={"status": "paid"})
    assert paid.json()["total"] == 1

    pending = await account.get(
        f"/sites/{site['id']}/orders", params={"status": "pending"}
    )
    assert pending.json()["total"] == 2
