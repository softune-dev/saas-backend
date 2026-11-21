"""Meta server-side events: payload quality, test mode, and tracking health."""

import hashlib
import json

import pytest

from app import marketing, queue
from app.marketing import build_purchase_payload, normalize_phone_e164, scrub
from app.worker import handle_send_meta_capi_event


def sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


CONTENTS = [
    {"id": "11111111-1111-1111-1111-111111111111", "quantity": 2, "item_price": 2792.0},
    {"id": "22222222-2222-2222-2222-222222222222", "quantity": 1, "item_price": 950.0},
]


def payload(**over):
    base = dict(
        event_id="ORD-1", event_time=1_700_000_000, value=6534.0, currency="BDT", order_number="ORD-1",
        contents=CONTENTS, customer_phone="+8801712345678", customer_email="Buyer@Example.com",
        first_name="Nusrat", last_name="Jahan", city="Dhaka", client_ip="203.0.113.9",
        user_agent="Mozilla/5.0", fbp="fb.1.1700000000000.123456789", fbc="fb.1.1700000000000.AbCdEf",
        event_source_url="https://shop.example/checkout",
    )
    base.update(over)
    return build_purchase_payload(**base)


# ---------------------------------------------------------------------------
#  Phone normalisation: the form Meta hashes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,want",
    [
        ("01712345678", "8801712345678"),
        ("+8801712345678", "8801712345678"),
        ("880 1712-345678", "8801712345678"),
        ("1712345678", "8801712345678"),
        ("+88001712345678", "8801712345678"),  # an accidental extra 0 after +880
        ("+91 98765 43210", "919876543210"),
        ("", ""),
        (None, ""),
    ],
)
def test_phone_is_normalised_to_international_digits(raw, want):
    assert normalize_phone_e164(raw) == want


# ---------------------------------------------------------------------------
#  The event itself
# ---------------------------------------------------------------------------


def test_customer_data_is_normalised_then_hashed():
    user = payload()["data"][0]["user_data"]
    assert user["ph"] == [sha("8801712345678")], "the local and international forms must hash the same"
    assert user["em"] == [sha("buyer@example.com")]
    assert user["fn"] == [sha("nusrat")] and user["ln"] == [sha("jahan")] and user["ct"] == [sha("dhaka")]
    assert user["country"] == [sha("bd")]
    # Not hashed: the IP and user agent, and the two browser identifiers.
    assert user["client_ip_address"] == "203.0.113.9" and user["fbp"].startswith("fb.1.")
    assert user["fbc"] == "fb.1.1700000000000.AbCdEf"


def test_local_and_international_phone_hash_identically():
    a = payload(customer_phone="01712345678")["data"][0]["user_data"]["ph"]
    b = payload(customer_phone="+8801712345678")["data"][0]["user_data"]["ph"]
    assert a == b


def test_no_raw_customer_data_is_sent():
    body = json.dumps(payload())
    for raw in ("01712345678", "8801712345678", "Buyer@Example.com", "buyer@example.com", "Nusrat", "Jahan", "Dhaka"):
        assert raw not in body, f"{raw!r} must only appear hashed"


def test_products_bought_are_sent_in_the_shape_catalog_ads_use():
    custom = payload()["data"][0]["custom_data"]
    assert custom["content_type"] == "product"
    assert custom["content_ids"] == [c["id"] for c in CONTENTS]
    assert custom["contents"] == CONTENTS
    assert custom["num_items"] == 3
    assert custom["value"] == 6534.0 and custom["currency"] == "BDT" and custom["order_id"] == "ORD-1"


def test_event_id_matches_the_browser_pixels_event_id():
    # The storefront fires fbq Purchase with eventID = order number; the server
    # event must use the same value or Meta counts the order twice.
    assert payload()["data"][0]["event_id"] == "ORD-1"


def test_missing_optional_data_is_simply_left_out():
    event = payload(customer_phone=None, customer_email=None, first_name=None, last_name=None, city=None,
                    fbp=None, fbc=None, contents=None)["data"][0]
    assert set(event["user_data"]) == {"country", "client_ip_address", "client_user_agent"}
    assert "content_type" not in event["custom_data"]


def test_test_event_code_is_included_only_while_set():
    assert payload(test_event_code="TEST12345")["test_event_code"] == "TEST12345"
    assert "test_event_code" not in payload()
    assert "test_event_code" not in payload(test_event_code=None)


# ---------------------------------------------------------------------------
#  The access token never leaks into stored or logged text
# ---------------------------------------------------------------------------


def test_token_is_removed_from_error_text():
    token = "EAAB" + "x" * 60
    text = f"Couldn't reach https://graph.facebook.com/v21.0/123/events?access_token={token} and {token} again"
    cleaned = scrub(text, token)
    assert token not in cleaned and "access_token=***" in cleaned


def test_error_text_is_trimmed():
    assert len(scrub("e" * 5000, None)) == 300


# ---------------------------------------------------------------------------
#  Settings and tracking health (database)
# ---------------------------------------------------------------------------

TOKEN = "EAAB" + "a1B2c3" * 12


async def _connected(account, site) -> dict:
    assert (await account.patch(f"/sites/{site['id']}", json={"seo": {"facebook_pixel": "123456789012345"}})).status_code == 200
    res = await account.post(f"/sites/{site['id']}/marketing/meta-capi", json={"access_token": TOKEN})
    assert res.status_code == 201, res.text
    return res.json()


async def _conn(account, site) -> dict:
    return (await account.get(f"/sites/{site['id']}/marketing")).json()[0]


async def test_test_event_code_can_be_set_and_cleared(account, site):
    conn = await _connected(account, site)
    url = f"/sites/{site['id']}/marketing/{conn['id']}"
    assert (await account.patch(url, json={"test_event_code": "TEST12345"})).json()["test_event_code"] == "TEST12345"
    assert (await account.patch(url, json={"test_event_code": None})).json()["test_event_code"] is None
    assert (await account.patch(url, json={"test_event_code": "bad code!"})).status_code == 422


async def test_worker_records_success_and_failure_on_the_connection(account, site, monkeypatch):
    conn = await _connected(account, site)
    await account.patch(f"/sites/{site['id']}/marketing/{conn['id']}", json={"test_event_code": "TEST777"})
    sent: list[dict] = []
    outcomes = iter([(True, None), (False, "Meta rejected the event (400): bad token")])

    async def fake_send(**kwargs):
        sent.append(kwargs)
        return next(outcomes)

    monkeypatch.setattr(marketing, "send_purchase_event", fake_send)
    job = {
        "site_id": site["id"], "event_id": "ORD-9", "event_time": 1_700_000_000, "value": 100.0, "currency": "BDT",
        "order_number": "ORD-9", "contents": CONTENTS, "customer_phone": "+8801712345678",
    }
    await handle_send_meta_capi_event(job)
    after_ok = await _conn(account, site)
    assert after_ok["events_sent"] == 1 and after_ok["events_failed"] == 0
    assert after_ok["last_event_at"] is not None and after_ok["last_error"] is None
    # The test code and the products reached the sender.
    assert sent[0]["test_event_code"] == "TEST777" and sent[0]["contents"] == CONTENTS

    await handle_send_meta_capi_event(job)
    after_fail = await _conn(account, site)
    assert after_fail["events_sent"] == 1 and after_fail["events_failed"] == 1
    assert "bad token" in after_fail["last_error"] and after_fail["last_error_at"] is not None


async def test_the_access_token_is_never_returned_by_the_api(account, site):
    await _connected(account, site)
    body = json.dumps((await account.get(f"/sites/{site['id']}/marketing")).json())
    assert TOKEN not in body


async def test_order_queues_the_event_with_products_and_browser_ids(account, site, monkeypatch):
    product = (await account.post(f"/sites/{site['id']}/products", json={"name": "Panjabi", "price_cents": 349000, "stock": 5})).json()
    host = (await account.post(f"/sites/{site['id']}/publish", json={})).json()["subdomain"]
    queued: list[tuple[str, dict]] = []

    async def spy(job, data=None, *a, **k):
        # Recorded and not forwarded: the test only needs to see what was queued.
        queued.append((job, data))

    monkeypatch.setattr(queue, "publish", spy)
    res = await account.client.post(
        f"/public/site/{host}/orders",
        json={
            "customer": {"first_name": "Nusrat", "last_name": "Jahan", "phone": "01711111111", "city": "Dhaka"},
            "items": [{"product_id": product["id"], "quantity": 2}],
            "fbp": "fb.1.1700000000000.123456789", "fbc": "fb.1.1700000000000.AbCdEf",
        },
    )
    assert res.status_code == 201, res.text
    job = next(d for j, d in queued if j == queue.JOB_SEND_META_CAPI_EVENT)
    assert job["contents"] == [{"id": product["id"], "quantity": 2, "item_price": 3490.0}]
    assert job["fbp"] == "fb.1.1700000000000.123456789" and job["fbc"] == "fb.1.1700000000000.AbCdEf"
    assert job["first_name"] == "Nusrat" and job["city"] == "Dhaka"
    assert job["event_id"] == res.json()["order_number"], "must match the pixel's eventID"


async def test_malformed_browser_ids_are_rejected(account, site):
    host = (await account.post(f"/sites/{site['id']}/publish", json={})).json()["subdomain"]
    product = (await account.post(f"/sites/{site['id']}/products", json={"name": "P", "price_cents": 1000, "stock": 5})).json()
    res = await account.client.post(
        f"/public/site/{host}/orders",
        json={"customer": {"phone": "01711111111"}, "items": [{"product_id": product["id"], "quantity": 1}], "fbp": "bad value<script>"},
    )
    assert res.status_code == 422
