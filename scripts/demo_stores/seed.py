"""Builds (or rebuilds) demo stores from catalog.py + urls/<slug>.json.

Runs on the server, inside a one-off api container that can see scripts/
(the api image itself only contains app/):

    docker compose -f docker-compose.prod.yml run --rm -v "$PWD/scripts:/app/scripts" api \\
        python scripts/demo_stores/seed.py voltix noor raaj pawpal ghorkona tinytots

Each store belongs to its own account, demo-<slug>@softunebd.com, on the
Business plan. A store that already exists is rebuilt only if it belongs to
that demo account: its categories, products and events are replaced and its
settings rewritten. A subdomain owned by anyone else is never touched.
"""

import asyncio
import json
import os
import secrets
import sys
from datetime import UTC, datetime

sys.path.insert(0, ".")
sys.path.insert(0, os.path.dirname(__file__))

from sqlalchemy import delete, select  # noqa: E402

from app import cache, queue  # noqa: E402
from app.crud import create_tenant_owner_and_site  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    Category, Event, PaymentConnection, Product, Site, SitePage, User,
)
from catalog import STORES  # noqa: E402
from catalog_bn import LOOK  # noqa: E402

# Store subdomains (<sub>.softunebd.com). "noor" is a real merchant's store,
# so the demo uses noormodest; the others are free names.
SUBDOMAIN = {"noor": "noormodest", "swarna": "swarnajewels", "oudh": "oudhhouse"}
LOCATIONS = [{"name": "Inside Dhaka", "charge_cents": 6000}, {"name": "Outside Dhaka", "charge_cents": 12000}]
FEATURES = [
    {"title": "Cash on delivery", "description": "Pay when it reaches you, anywhere in Bangladesh.", "icon": "truck"},
    {"title": "7-day easy returns", "description": "Not right? Return or exchange within 7 days.", "icon": "rotate-ccw"},
    {"title": "Quality checked", "description": "Every order is checked before it ships.", "icon": "shield-check"},
]
SECTIONS = ["banner", "hero", "categories", "featureProducts", "events", "productShowcase",
            "categoryShowcase", "whyChooseUs", "features", "bannerCta", "testimonials", "footer"]
PAGES = [("/", "home", "Home"), ("/shop", "products", "Shop"), ("/shop/:slug", "productDetail", "Product details"),
         ("/categories", "categories", "Categories"), ("/cart", "cart", "Cart"), ("/checkout", "checkout", "Checkout"),
         ("/about", "about", "About"), ("/contact", "contact", "Contact"), ("/faq", "faq", "FAQ"),
         ("/privacy", "privacy", "Privacy"), ("/terms", "terms", "Terms")]
REVIEWERS = [("Nusrat Jahan", "Dhaka"), ("Tanvir Hasan", "Chattogram"), ("Sadia Islam", "Sylhet")]


def slugify(text: str) -> str:
    out = "".join(c.lower() if c.isalnum() else "-" for c in text)
    while "--" in out:
        out = out.replace("--", "-")
    return out.strip("-")


def testimonials(s: dict) -> list[dict]:
    p = s["products"]
    quotes = [
        f"Ordered the {p[0][1]} and it looks even better in person. Delivered in two days, well packed.",
        f"Paid cash on delivery and checked everything first. The {p[4][1]} is great quality for the price.",
        f"Third order from {s['name']} now. Helpful on WhatsApp and the {p[8][1]} was exactly as shown.",
    ]
    return [{"id": f"t{i + 1}", "name": n, "role": city, "rating": 5 if i != 2 else 4, "image": "", "quote": q}
            for i, ((n, city), q) in enumerate(zip(REVIEWERS, quotes))]


# Category icons (Iconify Solar set), keyed by category slug; "store/slug"
# wins where two stores share a slug but sell different things.
CATEGORY_ICONS = {
    "abayas": "solar:t-shirt-bold", "hijabs": "solar:user-hands-bold", "kaftans": "solar:hanger-bold",
    "co-ords": "solar:hanger-2-bold", "prayer": "solar:moon-stars-bold", "accessories": "solar:bag-4-bold",
    "necklaces": "solar:crown-minimalistic-bold", "earrings": "solar:star-shine-bold", "bangles": "solar:record-circle-bold",
    "rings": "solar:medal-ribbons-star-bold", "bridal": "solar:heart-bold", "silver": "solar:diamond-bold",
    "perfumes": "solar:bottle-bold", "attar": "solar:waterdrop-bold", "oud": "solar:leaf-bold",
    "bakhoor": "solar:fire-bold", "gift-sets": "solar:gift-bold", "home": "solar:home-2-bold",
    "headphones": "solar:headphones-round-bold", "smartwatches": "solar:watch-square-bold",
    "phones": "solar:smartphone-bold", "laptops": "solar:laptop-bold", "speakers": "solar:speaker-bold",
    "power": "solar:battery-charge-bold",
    "vegetables": "solar:leaf-bold", "fruits": "solar:apple-bold", "rice-dal": "solar:bag-3-bold",
    "dairy": "solar:cup-bold", "snacks": "solar:donut-bold", "cleaning": "solar:trash-bin-minimalistic-bold",
    "toys": "solar:gamepad-bold", "plush": "solar:emoji-funny-circle-bold", "baby-care": "solar:heart-pulse-bold",
    "feeding": "solar:bottle-bold", "kids-wear": "solar:t-shirt-bold", "school": "solar:backpack-bold",
    "decor": "solar:pallete-2-bold", "cushions": "solar:sofa-bold", "lighting": "solar:lamp-bold",
    "dinnerware": "solar:plate-bold", "kitchen": "solar:chef-hat-bold", "plants": "solar:leaf-bold",
    "cat-food": "solar:cat-bold", "dog-food": "solar:bone-bold", "pawpal/toys": "solar:basketball-bold",
    "beds": "solar:bed-bold", "grooming": "solar:scissors-bold", "pawpal/accessories": "solar:tag-bold",
    "panjabi": "solar:t-shirt-bold", "koti": "solar:hanger-bold", "pajama": "solar:hanger-2-bold",
    "fatua": "solar:t-shirt-bold", "shawls": "solar:layers-bold", "footwear": "solar:walking-bold",
    "sneakers": "solar:running-bold", "formal": "solar:case-bold", "sandals": "solar:sun-bold",
    "women": "solar:women-bold", "kids": "solar:smile-circle-bold", "care": "solar:magic-stick-3-bold",
}
DEFAULT_CATEGORY_ICON = "solar:square-arrow-right-down-bold"


def theme(s: dict, u: dict, cat_ids: list[str], featured: list[str], showcase: str, event_ids: list[str]) -> dict:
    t = s["texts"]
    look = LOOK.get(s["slug"], {})
    ti = look.get("titles", {})
    (w1t, w1), (w2t, w2), (w3t, w3) = look.get("why", t["why"])
    cta = look.get("cta", (t["cta"][0], t["cta"][1], "Shop now"))
    fonts = look.get("fonts", {"display": s["fonts"]["display"], "body": s["fonts"]["body"]})
    colors = look.get("colors", s["colors"])
    return {
        "siteName": s["name"], "tagline": look.get("tagline", s["tagline"]),
        "logoType": "image", "logoImage": u["logo.png"],
        "primaryColor": colors["primary"], "accentColor": colors["accent"], "surfaceColor": colors["surface"],
        "displayFont": fonts["display"], "bodyFont": fonts["body"], "buttonStyle": "Pill",
        "announcementItems": look.get("announce", [
            "Free delivery on orders over ৳3,000",
            f"{s['events'][0][0]}: {s['events'][0][1]}% off for a limited time",
            "Cash on delivery all over Bangladesh",
        ]),
        "announcementDivider": "•",
        "navLinks": [{"id": "n1", "path": "/shop", "label": "Shop"}, {"id": "n2", "path": "/categories", "label": "Categories"},
                     {"id": "n3", "path": "/about", "label": "About"}, {"id": "n4", "path": "/contact", "label": "Contact"}],
        "headerButtons": [{"id": "b1", "label": "Cart", "style": "primary"}],
        "pages": [{"id": f"p{i + 1}", "path": p, "type": ty, "title": ti, "enabled": True} for i, (p, ty, ti) in enumerate(PAGES)],
        "sections": [{"id": f"s{i + 1}", "type": ty} for i, ty in enumerate(SECTIONS)],
        "heroMediaType": "image", "heroVideo": "",
        "heroImages": [u["hero-1.png"], u["hero-2.png"]],
        "heroImagesSquare": [u["hero-1-mobile.png"], u["hero-2-mobile.png"]],
        "categoriesTitle": ti.get("categories", "Shop by category"), "selectedCategoryIds": cat_ids,
        "featureProductsTitle": ti.get("featured", "Bestsellers"), "selectedProductIds": featured,
        "selectedEventIds": event_ids, "showcaseProductId": showcase,
        "categoryShowcaseTitle": ti.get("showcase", "Explore the collection"), "categoryShowcaseCategoryIds": cat_ids[:5],
        "whyTitle": ti.get("why", f"Why shop with {s['name']}"), "whyImage": u["why.png"],
        "why1Title": w1t, "why1": w1, "why2Title": w2t, "why2": w2, "why3Title": w3t, "why3": w3,
        "featuresTitle": ti.get("features", "Shopping made easy"),
        "feature1Title": "Fast delivery", "feature1": "Inside Dhaka in 24 hours, the rest of Bangladesh in 2 to 3 days.",
        "feature1IconKind": "icon", "feature1Icon": "solar:delivery-bold", "feature1Image": "",
        "feature2Title": "Cash on delivery", "feature2": "Check your order at the door and pay the rider. bKash and Nagad too.",
        "feature2IconKind": "icon", "feature2Icon": "solar:wallet-money-bold", "feature2Image": "",
        "feature3Title": "Easy returns", "feature3": "Not right? Return or exchange within 7 days, no fuss.",
        "feature3IconKind": "icon", "feature3Icon": "solar:restart-bold", "feature3Image": "",
        "ctaTitle": cta[0], "ctaBody": cta[1], "ctaButton": cta[2],
        "testimonialsTitle": ti.get("testimonials", "What our customers say"), "testimonialsMode": "cards",
        "testimonials": testimonials(s), "testimonialsCards": [], "testimonialsScreenshots": [],
        "footerDescription": look.get("about", t["about"]),
        "footerShopLabel": "Shop", "footerShopLinks": [{"id": "fs1", "path": "/shop", "label": "All products"},
                                                       {"id": "fs2", "path": "/categories", "label": "Categories"}],
        "footerCompanyLabel": "Company", "footerCompanyLinks": [{"id": "fc1", "path": "/about", "label": "About"},
                                                                {"id": "fc2", "path": "/contact", "label": "Contact"},
                                                                {"id": "fc3", "path": "/faq", "label": "FAQ"}],
    }


def site_fields(s: dict, u: dict, sub: str) -> dict:
    name = s["name"]
    return {
        "business": {
            "name": name, "type": s["niche"].split(":")[0].lower(),
            "email": f"hello@{sub}.demo", "phone": "01700000000", "whatsapp": "01700000000",
            "address": {"street": "House 12, Road 5", "region": "Gulshan 1", "city": "Dhaka", "postal_code": "1212", "country": "Bangladesh"},
            "socials": {"facebook": "https://facebook.com/", "instagram": "https://instagram.com/"},
            "opening_hours": ["Sa-Th 10:00 AM-9:00 PM"],
            "support_note": "This is a SoftuneBD demo store. Orders placed here are not shipped.",
        },
        "about": {"image": u["about.png"], "heading": s["tagline"],
                  "paragraphs": [s["texts"]["about"],
                                 "We deliver all over Bangladesh with cash on delivery, and we answer on WhatsApp every day.",
                                 "This is a demo store built on SoftuneBD to show what your own shop can look like."]},
        "faqs": [
            {"question": "How long does delivery take?", "answer": "Inside Dhaka within 24 hours, outside Dhaka in 2 to 3 working days."},
            {"question": "Can I pay cash on delivery?", "answer": "Yes, cash on delivery is available everywhere in Bangladesh. bKash and Nagad work too."},
            {"question": "Can I return or exchange?", "answer": "Yes, within 7 days if the item is unused and in its original packaging."},
            {"question": "Is delivery free?", "answer": "Delivery is free on orders over ৳3,000."},
        ],
        "legal": {
            "terms": {"title": "Terms of Service", "published": True, "content": f"By ordering from {name} you agree to give correct delivery details and to receive and pay for the order. Prices and stock can change without notice. This is a SoftuneBD demo store; orders are not processed."},
            "privacy": {"title": "Privacy Policy", "published": True, "content": f"{name} collects only what is needed to deliver your order: your name, phone number and address. We never sell your information. This is a SoftuneBD demo store."},
        },
        "shipping": {"locations": LOCATIONS, "free_over_cents": 300000},
    }


async def seed_one(db, s: dict) -> None:
    slug = s["slug"]
    sub = SUBDOMAIN.get(slug, slug)
    email = f"demo-{slug}@softunebd.com"
    with open(os.path.join(os.path.dirname(__file__), "urls", f"{slug}.json"), encoding="utf-8") as f:
        u = json.load(f)

    site = (await db.execute(select(Site).where(Site.subdomain == sub))).scalar_one_or_none()
    if site is not None:
        owner = (await db.execute(select(User).where(User.tenant_id == site.tenant_id, User.email == email))).scalar_one_or_none()
        if owner is None:
            print(f"{slug}: {sub} belongs to someone else, skipped")
            return
        # Rebuild: clear the old catalogue, keep the account and site.
        await db.execute(delete(Event).where(Event.site_id == site.id))
        await db.execute(delete(Product).where(Product.site_id == site.id))
        await db.execute(delete(Category).where(Category.site_id == site.id))
        await db.flush()
        print(f"{slug}: rebuilding {sub}")
    else:
        _user, created = await create_tenant_owner_and_site(
            db, email=email, password=secrets.token_urlsafe(24), workspace_name=s["name"], plan="business",
            template_key=s["theme"], site_name=s["name"], subdomain=sub, full_name=f"{s['name']} Demo",
        )
        site = await db.get(Site, created.id)
        print(f"{slug}: created {sub}")
    tenant_id, site_id = site.tenant_id, site.id

    cats: dict[str, Category] = {}
    for i, (cslug, name, desc) in enumerate(s["categories"]):
        img = u[f"category-{cslug}.png"]
        c = Category(tenant_id=tenant_id, site_id=site_id, name=name, slug=cslug, description=desc,
                     icon=CATEGORY_ICONS.get(f"{slug}/{cslug}") or CATEGORY_ICONS.get(cslug, DEFAULT_CATEGORY_ICON), image_url=img, banner_url=img, sort_order=i, is_active=True)
        db.add(c)
        cats[cslug] = c
    await db.flush()

    products: list[Product] = []
    for i, (cslug, name, price, compare, short) in enumerate(s["products"]):
        stock = 12 + (i * 7) % 40
        p = Product(
            tenant_id=tenant_id, site_id=site_id, category_id=cats[cslug].id,
            sku=f"{slug[:3].upper()}-{1001 + i}", name=name, slug=slugify(name),
            short_description=short,
            description=f"{short} Delivered all over Bangladesh with cash on delivery, and easy to return within 7 days.",
            price_cents=price * 100, compare_at_cents=compare * 100 if compare else None,
            cost_price_cents=int(price * 100 * 0.7), currency="BDT",
            stock=stock, track_stock=True, initial_sold_count=20 + (i * 13) % 90,
            images=[{"url": u[f"product-{i + 1:02d}.png"]}], attributes={}, is_active=True,
            free_delivery=False, delivery_charges=LOCATIONS, features=FEATURES,
        )
        db.add(p)
        products.append(p)
    await db.flush()

    by_cat = {c: [p for p, row in zip(products, s["products"]) if row[0] == c] for c in cats}
    cat_order = list(cats)
    events = []
    for i, (name, pct, _prompt, popup) in enumerate(s["events"]):
        # First event covers the first three categories, the second the rest.
        picks = cat_order[:3] if i == 0 else cat_order[3:]
        e = Event(tenant_id=tenant_id, site_id=site_id, name=name, slug=slugify(name),
                  description=f"{pct}% off selected {s['niche'].split(':')[0].lower()} for a limited time.",
                  image_url=u[f"event-{i + 1}.png"], cta_label="Shop now", discount_percent=pct,
                  is_active=True, is_popup=popup,
                  # Image only: no title, text or button on the card or popup,
                  # just the banner, which links to the event's products.
                  image_only=True)
        e.products = [p for c in picks for p in by_cat[c]]
        db.add(e)
        events.append(e)
    await db.flush()

    featured = [str(by_cat[c][0].id) for c in cat_order] + [str(by_cat[c][1].id) for c in cat_order[:2]]
    site.theme = {**(site.theme or {}), **theme(s, u, [str(c.id) for c in cats.values()], featured,
                                                 str(products[0].id), [str(e.id) for e in events])}
    for k, v in site_fields(s, u, sub).items():
        setattr(site, k, {**(getattr(site, k) or {}), **v} if isinstance(v, dict) and k == "business" else v)
    site.seo = {**(site.seo or {}), "keywords": f"{s['name']}, {s['niche']}, Bangladesh, cash on delivery",
                "sitemap_enabled": True, "noindex": False}
    has_cod = (await db.execute(select(PaymentConnection.id).where(
        PaymentConnection.site_id == site_id, PaymentConnection.provider == "cod"))).scalar_one_or_none()
    if not has_cod:
        db.add(PaymentConnection(tenant_id=tenant_id, site_id=site_id, provider="cod",
                                 label="Cash on Delivery", status="connected", config={}))

    for page in (await db.execute(select(SitePage).where(SitePage.site_id == site_id))).scalars().all():
        page.is_published = True
    site.status = "published"
    site.published_at = datetime.now(UTC)
    await db.commit()

    await cache.invalidate_site(site.subdomain, site.custom_domain)
    await cache.invalidate_dashboard(str(site_id))
    await queue.publish(queue.JOB_REVALIDATE_SITE, {"site_id": str(site_id), "paths": ["/"]})
    await queue.publish(queue.JOB_GENERATE_SITEMAP, {"site_id": str(site_id)})
    await queue.publish(queue.JOB_ATTACH_DOMAIN, {"site_id": str(site_id)})
    await queue.publish(queue.JOB_CAPTURE_SCREENSHOT, {"site_id": str(site_id)})
    print(f"{slug}: https://{sub}.softunebd.com ({len(cats)} categories, {len(products)} products, {len(events)} events)")


async def main() -> None:
    slugs = sys.argv[1:]
    if not slugs:
        raise SystemExit("usage: seed.py <slug> [<slug> ...]")
    by_slug = {s["slug"]: s for s in STORES}
    for slug in slugs:
        async with SessionLocal() as db:
            await seed_one(db, by_slug[slug])


if __name__ == "__main__":
    asyncio.run(main())
