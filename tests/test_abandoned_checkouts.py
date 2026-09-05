"""Abandoned checkout capture — data only, no automated follow-up. A
shopper's phone + cart is recorded once they pass phone validation during
checkout, before they submit the order (see app/api/public.py's
capture_abandoned_checkout)."""

from app import recaptcha


async def _product(account, site, **kw) -> dict:
    body = {"name": "Widget", "price_cents": 1000, "stock": 50, **kw}
    return (await account.post(f"/sites/{site['id']}/products", json=body)).json()


async def _publish(account, site) -> str:
    published = await account.post(f"/sites/{site['id']}/publish", json={})
    assert published.status_code == 200, published.text
    return published.json()["subdomain"]


def _no_recaptcha(monkeypatch):
    async def _fake_verify(*args, **kwargs):
        return recaptcha.VerifyResult(ok=True)

    monkeypatch.setattr(recaptcha, "verify", _fake_verify)


async def _capture(account, host: str, phone: str, items: list[dict]):
    return await account.client.post(
        f"/public/site/{host}/checkout/abandoned",
        json={"phone": phone, "items": items},
    )


async def test_capture_creates_a_row_with_resolved_product_name(account, site):
    product = await _product(account, site, name="Cotton Panjabi")
    host = await _publish(account, site)

    resp = await _capture(account, host, "01712345678", [{"product_id": product["id"], "quantity": 2}])
    assert resp.status_code == 204

    listed = await account.get(f"/sites/{site['id']}/abandoned-checkouts")
    assert listed.status_code == 200
    body = listed.json()
    assert body["total"] == 1
    row = body["items"][0]
    assert row["phone"] == "1712345678"
    assert row["items"] == [{"product_id": product["id"], "name": "Cotton Panjabi", "quantity": 2}]
    assert row["subtotal_cents"] == 2000
    assert row["converted_at"] is None


async def test_recapturing_same_phone_upserts_not_duplicates(account, site):
    product = await _product(account, site)
    host = await _publish(account, site)

    await _capture(account, host, "01712345678", [{"product_id": product["id"], "quantity": 1}])
    await _capture(account, host, "01712345678", [{"product_id": product["id"], "quantity": 3}])

    listed = (await account.get(f"/sites/{site['id']}/abandoned-checkouts")).json()
    assert listed["total"] == 1
    assert listed["items"][0]["subtotal_cents"] == 3000


async def test_invalid_phone_is_silently_ignored(account, site):
    product = await _product(account, site)
    host = await _publish(account, site)

    resp = await _capture(account, host, "not-a-phone", [{"product_id": product["id"], "quantity": 1}])
    assert resp.status_code == 204  # never surfaces an error to the shopper

    listed = (await account.get(f"/sites/{site['id']}/abandoned-checkouts")).json()
    assert listed["total"] == 0


async def test_completing_the_order_marks_it_converted(account, site, monkeypatch):
    _no_recaptcha(monkeypatch)
    product = await _product(account, site)
    host = await _publish(account, site)

    await _capture(account, host, "01712345678", [{"product_id": product["id"], "quantity": 1}])
    assert (await account.get(f"/sites/{site['id']}/abandoned-checkouts")).json()["total"] == 1

    order_resp = await account.client.post(
        f"/public/site/{host}/orders",
        json={
            "customer": {"first_name": "Buyer", "phone": "01712345678"},
            "items": [{"product_id": product["id"], "quantity": 1}],
        },
    )
    assert order_resp.status_code == 201, order_resp.text

    # Default list excludes converted rows — this shopper actually bought.
    listed = (await account.get(f"/sites/{site['id']}/abandoned-checkouts")).json()
    assert listed["total"] == 0
