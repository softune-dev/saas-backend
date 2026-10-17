"""AI store setup /apply — regression for the Bangla-name 500.

crud.slugify keeps only a-z0-9, so two Bangla category names used to get the
same slug; the 409 retry then read an expired ORM object (MissingGreenlet)
and the endpoint returned 500. Plan + images are seeded directly into the
tenant's state so this never calls Gemini.
"""

from sqlalchemy import select

from app import ai_site_setup as setup
from app.db import SessionLocal
from app.models import Category, Product, Tenant
from app.api.ai_setup import _slug_for

PLAN = setup._sanitize_plan(
    {},
    {
        "shop_name": "দেবটেস্ট",
        "sells": "মেয়েদের কুর্তি",
        "category_names": ["কুর্তি", "পায়জামা"],
        "product_name": "লাল মোহনী কুর্তি",
        "product_price_taka": 1500,
        "product_photo_url": None,
        "language": "bn",
        "notes": "",
    },
)


def test_slug_for_non_ascii_names_never_collide():
    a, b = _slug_for("কুর্তি", "category"), _slug_for("পায়জামা", "category")
    assert a != b and a.startswith("category-") and b.startswith("category-")
    assert _slug_for("Red Shirt", "category") == "red-shirt"


async def test_apply_creates_bangla_categories_and_product(account, site):
    inputs = {
        "shop_name": "দেবটেস্ট",
        "sells": "মেয়েদের কুর্তি",
        "category_names": ["কুর্তি", "পায়জামা"],
        "product_name": "লাল মোহনী কুর্তি",
        "product_price_taka": 1500,
        "product_photo_url": None,
        "language": "bn",
        "notes": "",
    }
    async with SessionLocal() as db:
        tenant = (await db.execute(select(Tenant).where(Tenant.id == account.tenant_id))).scalar_one()
        await setup.save_state(
            db, tenant,
            {"status": "planned", "runs": 1, "inputs": inputs, "plan": PLAN,
             "images": {}, "skipped": [], "created": {}},
        )

    res = await account.post("/ai/site-setup/apply")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "done"
    assert len(body["category_ids"]) == 2 and body["product_id"]

    async with SessionLocal() as db:
        cats = (await db.execute(select(Category.slug).where(Category.tenant_id == account.tenant_id))).scalars().all()
        prods = (await db.execute(select(Product.slug).where(Product.tenant_id == account.tenant_id))).scalars().all()
    assert len(set(cats)) == 2 and len(prods) == 1

    # Every save inside apply() must reach the database: the finished status
    # and BOTH category ids + the product id (the retry below relies on them).
    async with SessionLocal() as db:
        t = (await db.execute(select(Tenant).where(Tenant.id == account.tenant_id))).scalar_one()
        saved = t.settings["ai_site_setup"]
    assert saved["status"] == "done"
    assert len(saved["created"]["category_ids"]) == 2 and saved["created"]["product_id"]

    # A second apply (e.g. a retry) must not create duplicates.
    again = await account.post("/ai/site-setup/apply")
    assert again.status_code == 200, again.text
    async with SessionLocal() as db:
        n = (await db.execute(select(Category).where(Category.tenant_id == account.tenant_id))).scalars().all()
    assert len(n) == 2
