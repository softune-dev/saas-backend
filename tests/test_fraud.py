"""Fraud protection — phone/IP blocklists, device pending-lock/cooldown,
and the soft-flag review workflow (Suspicious Orders)."""

import uuid

from app import recaptcha
from app.fraud import evaluate_soft_flags


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


def _order_payload(phone: str, product_id: str, *, device_id: str | None = None, qty: int = 1):
    body = {
        "customer": {"first_name": "Buyer", "phone": phone},
        "items": [{"product_id": product_id, "quantity": qty}],
    }
    if device_id is not None:
        body["device_id"] = device_id
    return body


_ip_counter = iter(range(1, 65000))


async def _post_order(account, host, payload, *, ip: str | None = None):
    """POST an order with a distinct client IP per call by default.

    checkout's rate_limit dependency (app/ratelimit.py) is keyed on client
    IP, 8 requests / 5 minutes. httpx's ASGITransport gives every request in
    this test module the SAME client IP unless X-Forwarded-For is set, so
    without this every test in this file would share one 8-request budget
    and later tests would 429 — nothing to do with the fraud logic itself.
    Pass `ip` explicitly for tests that need a SPECIFIC address (the IP-block
    tests) or a SHARED one across calls (device-lock tests, which key off
    device_id, not IP, and don't care which IP is used as long as it's under
    the rate limit).
    """
    resolved_ip = ip or f"198.51.100.{next(_ip_counter) % 250 + 1}"
    return await account.client.post(
        f"/public/site/{host}/orders", json=payload, headers={"X-Forwarded-For": resolved_ip}
    )


# ---------------------------------------------------------------------------
#  evaluate_soft_flags — pure function unit tests
# ---------------------------------------------------------------------------


def test_evaluate_soft_flags_high_value_first_order():
    rules = {"hold_first_high_value": {"enabled": True, "value": 30}}  # 30 taka = 3000 cents
    status, reason = evaluate_soft_flags(
        is_first_order=True, total_cents=5000, prior_orders_in_window=0, rules=rules
    )
    assert (status, reason) == ("flagged", "high_value_first_order")


def test_evaluate_soft_flags_high_value_ignored_when_not_first_order():
    rules = {"hold_first_high_value": {"enabled": True, "value": 30}}
    status, reason = evaluate_soft_flags(
        is_first_order=False, total_cents=5000, prior_orders_in_window=0, rules=rules
    )
    assert (status, reason) == ("clear", None)


def test_evaluate_soft_flags_burst_orders():
    rules = {"flag_burst_orders": {"enabled": True, "value": 30}}
    status, reason = evaluate_soft_flags(
        is_first_order=False, total_cents=500, prior_orders_in_window=1, rules=rules
    )
    assert (status, reason) == ("flagged", "burst_orders")


def test_evaluate_soft_flags_clear_when_disabled():
    rules = {
        "hold_first_high_value": {"enabled": False, "value": 30},
        "flag_burst_orders": {"enabled": False, "value": 30},
    }
    status, reason = evaluate_soft_flags(
        is_first_order=True, total_cents=999999, prior_orders_in_window=5, rules=rules
    )
    assert (status, reason) == ("clear", None)


# ---------------------------------------------------------------------------
#  Phone blocklist — regression baseline (existing behavior, unchanged)
# ---------------------------------------------------------------------------


async def test_phone_blocklist_still_blocks_checkout(account, site, monkeypatch):
    _no_recaptcha(monkeypatch)
    product = await _product(account, site)
    add = await account.post(
        f"/sites/{site['id']}/fraud/blocklist", json={"phone": "01711111111", "note": "spam"}
    )
    assert add.status_code == 201
    host = await _publish(account, site)

    blocked = await _post_order(account, host, _order_payload("01711111111", product["id"]))
    assert blocked.status_code == 403

    allowed = await _post_order(account, host, _order_payload("01722222222", product["id"]))
    assert allowed.status_code == 201


# ---------------------------------------------------------------------------
#  IP blocklist CRUD + middleware enforcement
# ---------------------------------------------------------------------------


async def test_ip_blocklist_crud(account, site):
    added = await account.post(
        f"/sites/{site['id']}/fraud/ip-blocklist",
        json={"ip_address": "203.0.113.5", "note": "attacker"},
    )
    assert added.status_code == 201, added.text
    entry = added.json()
    assert entry["ip_address"] == "203.0.113.5"

    listed = await account.get(f"/sites/{site['id']}/fraud/ip-blocklist")
    assert len(listed.json()) == 1

    assert (
        await account.delete(f"/sites/{site['id']}/fraud/ip-blocklist/{entry['id']}")
    ).status_code == 204
    assert (await account.get(f"/sites/{site['id']}/fraud/ip-blocklist")).json() == []


async def test_ip_blocklist_rejects_cidr():
    from pydantic import ValidationError

    from app.schemas import FraudIpBlocklistEntryCreate

    try:
        FraudIpBlocklistEntryCreate(ip_address="203.0.113.0/24")
        raised = False
    except ValidationError:
        raised = True
    assert raised, "CIDR notation must be rejected in v1 (exact-IP only)"


async def test_ip_block_middleware_blocks_browsing_not_just_checkout(account, site, monkeypatch):
    _no_recaptcha(monkeypatch)
    product = await _product(account, site)
    host = await _publish(account, site)

    blocked_ip = "203.0.113.77"
    add = await account.post(
        f"/sites/{site['id']}/fraud/ip-blocklist", json={"ip_address": blocked_ip}
    )
    assert add.status_code == 201

    # Page load (not just checkout) — GET /public/site/{host} — must 403 too.
    resp = await account.client.get(
        f"/public/site/{host}", headers={"X-Forwarded-For": blocked_ip}
    )
    assert resp.status_code == 403

    # A different IP is unaffected.
    resp_ok = await account.client.get(
        f"/public/site/{host}", headers={"X-Forwarded-For": "198.51.100.9"}
    )
    assert resp_ok.status_code == 200

    # Checkout from the blocked IP is also rejected.
    order_resp = await account.client.post(
        f"/public/site/{host}/orders",
        json=_order_payload("01733333333", product["id"]),
        headers={"X-Forwarded-For": blocked_ip},
    )
    assert order_resp.status_code == 403

    # Removing the block clears cache and restores access.
    entry_id = add.json()["id"]
    assert (
        await account.delete(f"/sites/{site['id']}/fraud/ip-blocklist/{entry_id}")
    ).status_code == 204
    resp_after = await account.client.get(
        f"/public/site/{host}", headers={"X-Forwarded-For": blocked_ip}
    )
    assert resp_after.status_code == 200


# ---------------------------------------------------------------------------
#  Device pending-lock + cooldown
# ---------------------------------------------------------------------------


async def test_device_pending_lock_blocks_second_open_order(account, site, monkeypatch):
    _no_recaptcha(monkeypatch)
    product = await _product(account, site)
    rules_resp = await account.patch(
        f"/sites/{site['id']}",
        json={"fraud_rules": {"device_pending_lock": {"enabled": True}}},
    )
    assert rules_resp.status_code == 200
    host = await _publish(account, site)
    device_id = f"dev-{uuid.uuid4().hex[:12]}"

    first = await _post_order(
        account, host, _order_payload("01744444444", product["id"], device_id=device_id)
    )
    assert first.status_code == 201, first.text

    second = await _post_order(
        account, host, _order_payload("01744444444", product["id"], device_id=device_id)
    )
    assert second.status_code == 403

    # A different device is unaffected.
    other_device = await _post_order(
        account,
        host,
        _order_payload(
            "01755555555", product["id"], device_id=f"dev-{uuid.uuid4().hex[:12]}"
        ),
    )
    assert other_device.status_code == 201


async def test_device_id_omitted_is_backward_compatible(account, site, monkeypatch):
    """A storefront build that predates device_id must keep working exactly
    as before — the field is optional and the checks no-op without it."""
    _no_recaptcha(monkeypatch)
    product = await _product(account, site)
    await account.patch(
        f"/sites/{site['id']}",
        json={"fraud_rules": {"device_pending_lock": {"enabled": True}}},
    )
    host = await _publish(account, site)

    first = await _post_order(account, host, _order_payload("01766666666", product["id"]))
    assert first.status_code == 201
    second = await _post_order(account, host, _order_payload("01766666666", product["id"]))
    # No device_id sent by either request -> pending-lock has nothing to key
    # off, so the second order is NOT blocked (matches today's behavior).
    # PublicOrderOut deliberately never exposes device_id (public checkout
    # response, data-minimized) — check the merchant-side record instead.
    assert second.status_code == 201
    orders_list = (await account.get(f"/sites/{site['id']}/orders")).json()
    assert all(o["device_id"] is None for o in orders_list["items"])


async def test_device_cooldown_blocks_after_cancelled_order(account, site, monkeypatch):
    _no_recaptcha(monkeypatch)
    product = await _product(account, site)
    await account.patch(
        f"/sites/{site['id']}",
        json={"fraud_rules": {"device_cooldown": {"enabled": True, "value": 60}}},
    )
    host = await _publish(account, site)
    device_id = f"dev-{uuid.uuid4().hex[:12]}"

    first = await _post_order(
        account, host, _order_payload("01777777777", product["id"], device_id=device_id)
    )
    assert first.status_code == 201
    order_id = first.json()["order_number"]

    # Cancel it via the merchant-side order update.
    orders_list = (await account.get(f"/sites/{site['id']}/orders")).json()
    matching = next(o for o in orders_list["items"] if o["order_number"] == order_id)
    cancel = await account.patch(
        f"/sites/{site['id']}/orders/{matching['id']}", json={"status": "cancelled"}
    )
    assert cancel.status_code == 200

    second = await _post_order(
        account, host, _order_payload("01777777777", product["id"], device_id=device_id)
    )
    assert second.status_code == 403


# ---------------------------------------------------------------------------
#  Suspicious orders — soft-flag + review workflow
# ---------------------------------------------------------------------------


async def test_high_value_first_order_flagged_and_reviewable(account, site, monkeypatch):
    _no_recaptcha(monkeypatch)
    product = await _product(account, site, price_cents=5000)
    await account.patch(
        f"/sites/{site['id']}",
        json={"fraud_rules": {"hold_first_high_value": {"enabled": True, "value": 30}}},
    )
    host = await _publish(account, site)

    order_resp = await _post_order(account, host, _order_payload("01788888888", product["id"]))
    assert order_resp.status_code == 201
    assert order_resp.json()["order_number"]

    suspicious = await account.get(f"/sites/{site['id']}/fraud/suspicious-orders")
    assert suspicious.status_code == 200
    rows = suspicious.json()
    assert len(rows) == 1
    assert rows[0]["fraud_status"] == "flagged"
    assert rows[0]["fraud_reason"] == "high_value_first_order"

    # Same customer's SECOND high-value order is no longer "first" -> not
    # flagged. PublicOrderOut never exposes fraud_status (public checkout
    # response) — confirm via the suspicious-orders list staying at 1 instead.
    second = await _post_order(account, host, _order_payload("01788888888", product["id"]))
    assert second.status_code == 201
    still_one = (await account.get(f"/sites/{site['id']}/fraud/suspicious-orders")).json()
    assert len(still_one) == 1

    review = await account.post(
        f"/sites/{site['id']}/fraud/suspicious-orders/{rows[0]['id']}/review",
        json={"decision": "cleared"},
    )
    assert review.status_code == 200
    assert review.json()["fraud_status"] == "cleared"

    still_suspicious = (await account.get(f"/sites/{site['id']}/fraud/suspicious-orders")).json()
    assert still_suspicious == []

    # One-shot: reviewing again 400s rather than silently no-op-ing.
    again = await account.post(
        f"/sites/{site['id']}/fraud/suspicious-orders/{rows[0]['id']}/review",
        json={"decision": "confirmed_fraud"},
    )
    assert again.status_code == 400


async def test_confirmed_fraud_does_not_touch_blocklist(account, site, monkeypatch):
    """Locked-in product decision: confirming fraud is pure metadata, it
    must never silently add to the phone/IP blocklist."""
    _no_recaptcha(monkeypatch)
    product = await _product(account, site, price_cents=5000)
    await account.patch(
        f"/sites/{site['id']}",
        json={"fraud_rules": {"hold_first_high_value": {"enabled": True, "value": 30}}},
    )
    host = await _publish(account, site)
    order_resp = await _post_order(account, host, _order_payload("01799999999", product["id"]))
    order_id = order_resp.json()["order_number"]
    suspicious = (await account.get(f"/sites/{site['id']}/fraud/suspicious-orders")).json()
    row = next(r for r in suspicious if r["order_number"] == order_id)

    await account.post(
        f"/sites/{site['id']}/fraud/suspicious-orders/{row['id']}/review",
        json={"decision": "confirmed_fraud"},
    )

    blocklist = (await account.get(f"/sites/{site['id']}/fraud/blocklist")).json()
    ip_blocklist = (await account.get(f"/sites/{site['id']}/fraud/ip-blocklist")).json()
    assert blocklist == []
    assert ip_blocklist == []


# ---------------------------------------------------------------------------
#  Fail-open — Redis errors must never break a public request
# ---------------------------------------------------------------------------


async def test_ip_block_fails_open_on_cache_error(account, site, monkeypatch):
    """Simulates a real Redis outage (the client() call itself fails), not a
    broken get_json — cache.py's own real get_json/set_json already swallow
    that internally (see their module docstring), so this proves BOTH the
    ip_block middleware and the unrelated site-config cache read degrade
    gracefully together, the way an actual Redis blip would behave."""
    from app import cache as cache_module

    _no_recaptcha(monkeypatch)
    product = await _product(account, site)
    host = await _publish(account, site)

    def _raise_client():
        raise ConnectionError("redis unreachable")

    monkeypatch.setattr(cache_module, "client", _raise_client)

    resp = await account.client.get(
        f"/public/site/{host}", headers={"X-Forwarded-For": "203.0.113.201"}
    )
    assert resp.status_code == 200

    order_resp = await account.client.post(
        f"/public/site/{host}/orders",
        json=_order_payload("01700000001", product["id"]),
        headers={"X-Forwarded-For": "203.0.113.201"},
    )
    assert order_resp.status_code == 201
