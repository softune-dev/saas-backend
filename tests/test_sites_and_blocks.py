"""Sites, pages, block validation, and the public render endpoint."""

import uuid


# ---------------------------------------------------------------------------
#  Sites
# ---------------------------------------------------------------------------


async def test_templates_are_listed(client):
    response = await client.get("/templates")
    assert response.status_code == 200
    assert len(response.json()) > 0, "run migrations/004_seed.sql"
    assert {"key", "name", "framework", "block_types"} <= set(response.json()[0])


async def test_new_site_copies_template_defaults(site):
    """Defaults are COPIED, not referenced — see app/api/sites.py create_site."""
    assert site["status"] == "draft"
    assert site["theme"], "theme should be seeded from the template"
    assert site["seo"]["noindex"] is True, "drafts must not be indexed by Google"


async def test_new_site_gets_default_pages(account, site):
    pages = await account.get(f"/sites/{site['id']}/pages")
    assert pages.status_code == 200
    assert len(pages.json()) >= 1
    assert pages.json()[0]["blocks"], "default page should arrive with blocks"


async def test_reserved_subdomain_is_rejected(account, template_id):
    response = await account.post(
        "/sites", json={"template_id": template_id, "name": "X", "subdomain": "admin"},
    )
    assert response.status_code == 422


async def test_duplicate_subdomain_is_rejected(account, template_id, site):
    response = await account.post(
        "/sites",
        json={
            "template_id": template_id,
            "name": "Copycat",
            "subdomain": site["subdomain"],
        },
    )
    assert response.status_code == 409
    assert "taken" in response.json()["detail"].lower()


async def test_invalid_subdomain_format_is_rejected(account, template_id):
    for bad in ["UPPER", "has space", "-leading", "a", "has_underscore"]:
        response = await account.post(
            "/sites",
            json={"template_id": template_id, "name": "X", "subdomain": bad},
        )
        assert response.status_code == 422, f"{bad!r} should have been rejected"


async def test_patch_only_changes_sent_fields(account, site):
    """exclude_unset in action: name changes, theme survives untouched."""
    original_theme = site["theme"]
    response = await account.patch(f"/sites/{site['id']}", json={"name": "Renamed"})
    assert response.status_code == 200
    assert response.json()["name"] == "Renamed"
    assert response.json()["theme"] == original_theme


async def test_publish_flow(account, site):
    response = await account.post(f"/sites/{site['id']}/publish")
    assert response.status_code == 200
    assert response.json()["status"] == "published"
    assert response.json()["published_at"]
    assert response.json()["seo"]["noindex"] is False

    pages = await account.get(f"/sites/{site['id']}/pages")
    assert all(p["is_published"] for p in pages.json())

    unpub = await account.post(f"/sites/{site['id']}/unpublish")
    assert unpub.json()["status"] == "draft"
    assert unpub.json()["seo"]["noindex"] is True


# ---------------------------------------------------------------------------
#  Block registry + validation
# ---------------------------------------------------------------------------


async def test_block_registry_is_exposed(client):
    """The admin panel builds every edit form from this response."""
    response = await client.get("/blocks")
    assert response.status_code == 200
    blocks = response.json()["blocks"]
    assert len(blocks) > 5

    hero = next(b for b in blocks if b["type"] == "Hero")
    field_names = [f["name"] for f in hero["fields"]]
    assert "heading" in field_names
    assert all({"name", "type", "label", "required"} <= set(f) for f in hero["fields"])


async def test_valid_blocks_are_accepted(account, site):
    response = await account.post(
        f"/sites/{site['id']}/pages",
        json={
            "slug": "about",
            "title": "About",
            "blocks": [
                {"type": "Hero", "data": {
                    "heading": "Hello", "subheading": "World", "align": "left",
                }},
                {"type": "CTA", "data": {
                    "heading": "Ready?", "cta_text": "Go", "cta_link": "https://x.test",
                }},
            ],
        },
    )
    assert response.status_code == 201, response.text
    assert len(response.json()["blocks"]) == 2


async def test_unknown_block_type_is_rejected(account, site):
    response = await account.post(
        f"/sites/{site['id']}/pages",
        json={"slug": "bad", "title": "Bad",
              "blocks": [{"type": "NotARealBlock", "data": {}}]},
    )
    assert response.status_code == 422
    assert "unknown block type" in response.json()["detail"].lower()


async def test_missing_required_field_is_rejected(account, site):
    """Hero.heading is required — the registry is what enforces it."""
    response = await account.post(
        f"/sites/{site['id']}/pages",
        json={"slug": "noheading", "title": "No heading",
              "blocks": [{"type": "Hero", "data": {"subheading": "orphan"}}]},
    )
    assert response.status_code == 422
    assert "required" in response.json()["detail"].lower()


async def test_invalid_select_value_is_rejected(account, site):
    response = await account.post(
        f"/sites/{site['id']}/pages",
        json={"slug": "badalign", "title": "Bad align",
              "blocks": [{"type": "Hero", "data": {"heading": "Hi", "align": "sideways"}}]},
    )
    assert response.status_code == 422


async def test_overlong_text_is_rejected(account, site):
    response = await account.post(
        f"/sites/{site['id']}/pages",
        json={"slug": "toolong", "title": "Too long",
              "blocks": [{"type": "Hero", "data": {"heading": "x" * 500}}]},
    )
    assert response.status_code == 422
    assert "too long" in response.json()["detail"].lower()


async def test_unknown_fields_are_stripped(account, site):
    """Normalisation: junk keys must not accumulate in JSONB over the years."""
    response = await account.post(
        f"/sites/{site['id']}/pages",
        json={"slug": "extra", "title": "Extra",
              "blocks": [{"type": "Hero", "data": {
                  "heading": "Hi", "totally_made_up_key": "should vanish"}}]},
    )
    assert response.status_code == 201
    assert "totally_made_up_key" not in response.json()["blocks"][0]["data"]


async def test_nested_list_blocks_validate(account, site):
    """FAQ.items is a `list` field of sub-fields — recursion has to work."""
    response = await account.post(
        f"/sites/{site['id']}/pages",
        json={"slug": "faq", "title": "FAQ", "blocks": [
            {"type": "FAQ", "data": {"heading": "Q&A", "items": [
                {"question": "Is it fast?", "answer": "Yes."},
                {"question": "Really?", "answer": "Very."},
            ]}}
        ]},
    )
    assert response.status_code == 201
    assert len(response.json()["blocks"][0]["data"]["items"]) == 2

    bad = await account.post(
        f"/sites/{site['id']}/pages",
        json={"slug": "faq2", "title": "FAQ2", "blocks": [
            {"type": "FAQ", "data": {"items": [{"question": "No answer given"}]}}
        ]},
    )
    assert bad.status_code == 422


async def test_duplicate_page_slug_is_rejected(account, site):
    body = {"slug": "dupe", "title": "First", "blocks": []}
    assert (await account.post(f"/sites/{site['id']}/pages", json=body)).status_code == 201
    second = await account.post(f"/sites/{site['id']}/pages", json=body)
    assert second.status_code == 409


# ---------------------------------------------------------------------------
#  Public render endpoint
# ---------------------------------------------------------------------------


async def test_unpublished_site_is_not_public(client, site):
    response = await client.get(f"/public/site/{site['subdomain']}")
    assert response.status_code == 404


async def test_published_site_serves_full_config(client, account, site):
    await account.patch(
        f"/sites/{site['id']}",
        json={"business": {
            "name": "Acme Ltd", "phone": "+1 555 0100",
            "address": {"street": "1 Main St", "city": "Springfield"},
        }},
    )
    await account.post(f"/sites/{site['id']}/publish")

    response = await client.get(f"/public/site/{site['subdomain']}")
    assert response.status_code == 200
    body = response.json()

    assert body["site"]["theme"]
    assert body["nav"]
    assert body["pages"]

    # SEO is resolved server-side so every template gets identical metadata.
    seo = body["pages"][0]["seo"]
    assert seo["title"]
    assert seo["canonical"].startswith("https://")
    assert seo["noindex"] is False

    # JSON-LD generated from business details the user entered once.
    assert body["json_ld"]["@type"] == "LocalBusiness"
    assert body["json_ld"]["name"] == "Acme Ltd"
    assert body["json_ld"]["address"]["addressLocality"] == "Springfield"


async def test_sitemap_lists_indexable_pages(client, account, site):
    await account.post(f"/sites/{site['id']}/publish")
    response = await client.get(f"/public/site/{site['subdomain']}/sitemap.xml")
    assert response.status_code == 200
    urls = response.json()["urls"]
    assert urls
    assert all(u["loc"].startswith("https://") for u in urls)


async def test_missing_host_returns_404(client):
    response = await client.get(f"/public/site/nope-{uuid.uuid4().hex[:8]}")
    assert response.status_code == 404
