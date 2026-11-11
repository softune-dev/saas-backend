"""Softune Pay: SMS parsing, pairing, and SMS-to-order matching.

The parsing tests need no database. The flow tests run against the real
database like the rest of the suite and clean up their own tenant."""

import uuid

from app import pay_matching, recaptcha

BKASH = "You have received Tk 1,450.00 from 01712344521. Fee Tk 0.00. Balance Tk 12,830.50. TrxID BKD7H2K9QX at 11/11/2026 14:02"
NAGAD = "Money Received. Amount: Tk 890.00 Sender: 01811230917 Ref: N/A TxnID: 71KQ8ZP3 Balance: Tk 4,210.00 11/11/2026 13:55"
ROCKET = "Tk600.00 received from A/C:016XXXX7702 Fee:Tk0, Your A/C Balance: Tk1,980.00 TxnId:2219044871 Date:11-NOV-26 01:21:10 pm"


# ---------------------------------------------------------------------------
#  Parsing (no database)
# ---------------------------------------------------------------------------


def test_parses_all_three_wallets():
    b = pay_matching.parse_sms("bKash", BKASH)
    assert (b.provider, b.trx_id, b.amount_cents, b.sender_number) == ("bkash", "BKD7H2K9QX", 145_000, "01712344521")
    n = pay_matching.parse_sms("NAGAD", NAGAD)
    assert (n.provider, n.trx_id, n.amount_cents) == ("nagad", "71KQ8ZP3", 89_000)
    r = pay_matching.parse_sms("16216", ROCKET)
    assert (r.provider, r.trx_id, r.amount_cents) == ("rocket", "2219044871", 60_000)


def test_ignores_fake_senders_outgoing_money_and_noise():
    # A real-looking bKash text from an ordinary number must never count.
    assert pay_matching.parse_sms("01799999999", BKASH) is None
    assert pay_matching.parse_sms("bKash", "Send Money Tk 500.00 to 01712344521 successful. TrxID ABC123XYZ") is None
    assert pay_matching.parse_sms("bKash", "Your bKash verification code is 123456") is None
    assert pay_matching.parse_sms("bKash", "You have received Tk 0.00 from 01712344521. TrxID ZERO000000") is None


def test_masks_numbers():
    assert pay_matching.mask_number("01712344521") == "017••••4521"


# ---------------------------------------------------------------------------
#  Flow (real database)
# ---------------------------------------------------------------------------


async def _pair(account, site) -> tuple[str, dict]:
    code = (await account.post(f"/sites/{site['id']}/pay/pairing-code")).json()["code"]
    paired = await account.client.post("/pay/pair", json={"code": code, "device_name": "Test phone", "app_version": "0.1.0"})
    assert paired.status_code == 200, paired.text
    body = paired.json()
    return body["token"], body


async def _manual_order(account, site, monkeypatch, trx: str, price_cents: int) -> dict:
    async def _ok(*a, **k):
        return recaptcha.VerifyResult(ok=True)

    monkeypatch.setattr(recaptcha, "verify", _ok)
    product = (await account.post(f"/sites/{site['id']}/products", json={"name": "Saree", "price_cents": price_cents, "stock": 5})).json()
    host = (await account.post(f"/sites/{site['id']}/publish", json={})).json()["subdomain"]
    placed = await account.client.post(
        f"/public/site/{host}/orders",
        json={
            "customer": {"first_name": "Nusrat", "phone": "01712344521"},
            "items": [{"product_id": product["id"], "quantity": 1}],
            "payment_method": "manual",
            "transaction_id": trx,
        },
        headers={"X-Forwarded-For": f"203.0.113.{uuid.uuid4().int % 250 + 1}"},
    )
    assert placed.status_code in (200, 201), placed.text
    return placed.json()


async def _order_status(account, site, number: str) -> str:
    listing = (await account.get(f"/sites/{site['id']}/orders", params={"limit": 50})).json()
    return next(o["status"] for o in listing["items"] if o["order_number"] == number)


async def test_pairing_code_works_once_and_returns_store(account, site):
    code = (await account.post(f"/sites/{site['id']}/pay/pairing-code")).json()["code"]
    first = await account.client.post("/pay/pair", json={"code": code})
    assert first.status_code == 200
    assert first.json()["store"]["name"] == site["name"]
    again = await account.client.post("/pay/pair", json={"code": code})
    assert again.status_code == 400


async def test_sms_after_order_marks_it_paid(account, site, monkeypatch):
    order = await _manual_order(account, site, monkeypatch, "BKD7H2K9QX", 145_000)
    token, _ = await _pair(account, site)
    sms = BKASH.replace("1,450.00", f"{order['total_cents'] / 100:,.2f}")
    r = await account.client.post("/pay/sms", json={"sender": "bKash", "body": sms}, headers={"Authorization": f"Bearer {token}"})
    assert r.json()["status"] == "matched", r.text
    assert await _order_status(account, site, order["order_number"]) == "paid"
    # The same SMS again is counted once, never twice.
    again = await account.client.post("/pay/sms", json={"sender": "bKash", "body": sms}, headers={"Authorization": f"Bearer {token}"})
    assert again.json()["result"] == "duplicate"


async def test_sms_before_order_matches_at_checkout(account, site, monkeypatch):
    token, _ = await _pair(account, site)
    # Price chosen so the SMS amount equals the order total (product + delivery).
    first = await account.client.post(
        "/pay/sms", json={"sender": "NAGAD", "body": NAGAD.replace("71KQ8ZP3", "EARLY0001")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert first.json()["status"] == "unmatched"
    order = await _manual_order(account, site, monkeypatch, "early0001", 89_000)
    expected = "paid" if order["total_cents"] == 89_000 else "pending"
    assert await _order_status(account, site, order["order_number"]) == expected


async def test_wrong_amount_is_never_auto_confirmed(account, site, monkeypatch):
    order = await _manual_order(account, site, monkeypatch, "SHORTPAY01", 200_000)
    token, _ = await _pair(account, site)
    body = BKASH.replace("BKD7H2K9QX", "SHORTPAY01").replace("1,450.00", "100.00")
    r = await account.client.post("/pay/sms", json={"sender": "bKash", "body": body}, headers={"Authorization": f"Bearer {token}"})
    assert r.json()["status"] == "mismatch"
    assert await _order_status(account, site, order["order_number"]) == "pending"


async def test_removed_phone_is_locked_out(account, site):
    token, body = await _pair(account, site)
    assert (await account.delete(f"/sites/{site['id']}/pay/devices/{body['device_id']}")).status_code == 204
    r = await account.client.get("/pay/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401
