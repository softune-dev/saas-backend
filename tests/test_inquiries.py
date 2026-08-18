"""Contact form submissions — the public/anonymous write path."""


async def test_contact_form_submission_and_inbox(client, account, site):
    await account.post(f"/sites/{site['id']}/publish")

    response = await client.post(
        f"/public/site/{site['subdomain']}/contact",
        json={"data": {"name": "Jane", "email": "jane@example.test", "message": "Hello!"}},
    )
    assert response.status_code == 201
    assert response.json()["status"] == "new"

    inbox = await account.get(f"/sites/{site['id']}/inquiries")
    assert inbox.status_code == 200
    assert inbox.json()["total"] == 1
    assert inbox.json()["items"][0]["data"]["name"] == "Jane"


async def test_contact_form_rejected_on_unpublished_site(client, site):
    response = await client.post(
        f"/public/site/{site['subdomain']}/contact",
        json={"data": {"message": "anyone home?"}},
    )
    assert response.status_code == 404


async def test_oversized_field_is_rejected(client, account, site):
    await account.post(f"/sites/{site['id']}/publish")
    response = await client.post(
        f"/public/site/{site['subdomain']}/contact",
        json={"data": {"message": "x" * 6000}},
    )
    assert response.status_code == 422


async def test_mark_inquiry_status(account, site, client):
    await account.post(f"/sites/{site['id']}/publish")
    created = (
        await client.post(
            f"/public/site/{site['subdomain']}/contact",
            json={"data": {"message": "test"}},
        )
    ).json()

    updated = await account.patch(
        f"/sites/{site['id']}/inquiries/{created['id']}", params={"status": "read"}
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "read"

    bad = await account.patch(
        f"/sites/{site['id']}/inquiries/{created['id']}", params={"status": "bogus"}
    )
    assert bad.status_code == 422
