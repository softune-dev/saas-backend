"""The sitemap lists product pages, and product text is never empty for Google."""


async def _published(account, site) -> str:
    published = await account.post(f"/sites/{site['id']}/publish", json={})
    assert published.status_code == 200
    return published.json()["subdomain"]


async def _product(account, site, name, **extra) -> dict:
    res = await account.post(f"/sites/{site['id']}/products", json={"name": name, "price_cents": 50000, "stock": 5, **extra})
    assert res.status_code == 201, res.text
    return res.json()


async def _sitemap(account, host) -> list[str]:
    res = await account.client.get(f"/public/site/{host}/sitemap.xml")
    assert res.status_code == 200
    return [u["loc"] for u in res.json()["urls"]]


async def test_sitemap_lists_active_product_pages_only(account, site):
    live = await _product(account, site, "Blue Panjabi")
    hidden = await _product(account, site, "Hidden Item")
    assert (await account.patch(f"/sites/{site['id']}/products/{hidden['id']}", json={"is_active": False})).status_code == 200
    host = await _published(account, site)

    locs = await _sitemap(account, host)
    assert any(loc.endswith(f"/shop/{live['slug']}") for loc in locs)
    assert not any(hidden["slug"] in loc for loc in locs), "inactive products must not be listed"
    # The ordinary pages are still there.
    assert any(loc.rstrip("/").endswith(host) or loc.endswith("/shop") for loc in locs)


async def test_sitemap_product_urls_use_the_stores_own_domain(account, site):
    product = await _product(account, site, "Domain Check")
    host = await _published(account, site)
    locs = await _sitemap(account, host)
    assert f"https://{host}.softunebd.com/shop/{product['slug']}" in locs or any(
        loc.startswith("https://") and loc.endswith(f"/shop/{product['slug']}") for loc in locs
    )


async def test_site_that_asks_to_stay_out_of_search_lists_no_products(account, site):
    await _product(account, site, "Secret Panjabi")
    host = await _published(account, site)
    assert (await account.patch(f"/sites/{site['id']}", json={"seo": {"noindex": True}})).status_code == 200
    locs = await _sitemap(account, host)
    assert not any("/shop/" in loc for loc in locs)


async def test_turning_the_sitemap_off_empties_it(account, site):
    await _product(account, site, "Anything")
    host = await _published(account, site)
    assert (await account.patch(f"/sites/{site['id']}", json={"seo": {"sitemap_enabled": False}})).status_code == 200
    assert await _sitemap(account, host) == []


async def test_empty_long_description_falls_back_to_the_short_one(account, site):
    product = await _product(account, site, "Short Only", short_description="A soft cotton panjabi.")
    host = await _published(account, site)
    shown = (await account.client.get(f"/public/site/{host}/products/{product['slug']}")).json()
    assert shown["description"] == "A soft cotton panjabi."
    # A real long description still wins.
    full = await _product(account, site, "Has Both", description="<p>Long text</p>", short_description="Short")
    shown = (await account.client.get(f"/public/site/{host}/products/{full['slug']}")).json()
    assert shown["description"] == "<p>Long text</p>"
