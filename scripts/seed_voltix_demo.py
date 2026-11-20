"""Build the "Voltix" electronics demo store (theme 2, bazaar) in one go.

The landing site's /demo page links to it as the electronics example, next to
Elara (theme 3) and Rivelle (theme 1). Every homepage section is filled:
announcement bar, hero, categories, featured products, a sale event, the
product and category showcases, why-choose-us, features, a call to action,
testimonials and the footer, plus About, FAQ, Terms and Privacy. Nothing is
left as a template placeholder.

Photos are free stock photos from Unsplash (free for commercial use), linked
directly; the storefront already allows images.unsplash.com. Product names
are our own store brand, not real brands.

Run once, on the server (it writes to the production database):

    docker compose -f docker-compose.prod.yml exec -T api python - < scripts/seed_voltix_demo.py

(The image only contains app/, so the script is piped in from the host.)

Safe to re-run: if voltix already exists it stops without changing anything.
The owner login gets a random password nobody knows; use "Forgot password"
on demo-voltix@softunebd.com if anyone ever needs into its dashboard.
"""

import asyncio
import secrets
import sys
from datetime import UTC, datetime

sys.path.insert(0, ".")

from sqlalchemy import select  # noqa: E402

from app import cache, queue  # noqa: E402
from app.crud import create_tenant_owner_and_site  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import Category, Event, PaymentConnection, Product, Site, SitePage  # noqa: E402

SUBDOMAIN = "voltix"
NAME = "Voltix"
EMAIL = "demo-voltix@softunebd.com"


def photo(pid: str, w: int = 900, h: int = 900) -> str:
    return f"https://images.unsplash.com/{pid}?auto=format&fit=crop&w={w}&h={h}&q=80"


# (slug, name, description, icon, cover photo)
CATEGORIES = [
    ("headphones", "Headphones", "Over-ear and on-ear sound, wired and wireless", "solar:headphones-round-bold", "photo-1505740420928-5e560c06d30e"),
    ("earbuds", "Earbuds", "True wireless earbuds with charging cases", "solar:headphones-square-bold", "photo-1606220588913-b3aacb4d2f46"),
    ("smartwatches", "Smartwatches", "Fitness, calls and notifications on your wrist", "solar:clock-circle-bold", "photo-1579586337278-3befd40fd17a"),
    ("smartphones", "Smartphones", "Phones for every budget, official warranty", "solar:smartphone-bold", "photo-1511707171634-5f897ff02aa9"),
    ("laptops", "Laptops", "Work, study and creative machines", "solar:laptop-bold", "photo-1496181133206-80ce9b88a853"),
    ("speakers", "Speakers", "Portable Bluetooth and home speakers", "solar:music-note-bold", "photo-1608043152269-423dbba4e7e1"),
    ("cameras", "Cameras", "DSLR and mirrorless cameras with lenses", "solar:camera-bold", "photo-1516035069371-29a1b244cc32"),
    ("gaming", "Gaming", "Controllers and gear for console and PC", "solar:gamepad-bold", "photo-1612287230202-1ff1d85d1bdf"),
    ("keyboards", "Keyboards", "Mechanical keyboards that feel great to type on", "solar:keyboard-bold", "photo-1618384887929-16ec33fab9ef"),
    ("power", "Power & Charging", "Power banks, chargers and cables", "solar:battery-charge-bold", "photo-1760708825913-65a50b3dc39b"),
]

# (category slug, name, price ৳, compare-at ৳ or None, stock, sold, [photos], short description)
PRODUCTS = [
    ("headphones", "Voltix Aura ANC Wireless Headphones", 8990, 10990, 24, 63, ["photo-1505740420928-5e560c06d30e", "photo-1583394838336-acd977736f90"], "Active noise cancelling, 40-hour battery, fast USB-C charging."),
    ("headphones", "Voltix Studio Over-Ear Headphones", 5490, 6490, 31, 41, ["photo-1618366712010-f4ae9c647dcb", "photo-1585298723682-7115561c51b7"], "Deep bass, soft memory-foam cushions, foldable for travel."),
    ("headphones", "Voltix Pastel Wireless Headphones", 4290, 4990, 18, 27, ["photo-1613040809024-b4ef7ba99bc3"], "Lightweight Bluetooth 5.3 headphones in soft pink and white."),
    ("earbuds", "Voltix Pods Pro Wireless Earbuds", 3990, 4990, 40, 112, ["photo-1606741965326-cb990ae01bb2", "photo-1606841837239-c5a1a4a07af7"], "ANC earbuds with transparency mode and a wireless charging case."),
    ("earbuds", "Voltix Beat Buds Sport", 2490, 2990, 52, 88, ["photo-1606220588913-b3aacb4d2f46"], "Sweat-proof sport earbuds with secure ear hooks, 30 hours total."),
    ("earbuds", "Voltix Mini True Wireless Earbuds", 1590, 1990, 66, 140, ["photo-1572569511254-d8f925fe2cbb"], "Pocket-size earbuds with clear calls and a one-tap pairing case."),
    ("smartwatches", "Voltix Watch S2 AMOLED", 6490, 7990, 22, 54, ["photo-1579586337278-3befd40fd17a", "photo-1624096104992-9b4fa3a279dd"], "1.9-inch AMOLED display, Bluetooth calls, heart rate and SpO2."),
    ("smartwatches", "Voltix Watch Active", 3990, 4790, 35, 77, ["photo-1546868871-7041f2a55e12"], "100+ sport modes, 10-day battery and water resistance."),
    ("smartwatches", "Voltix Watch Classic", 4590, None, 14, 19, ["photo-1637160151663-a410315e4e75"], "Clean everyday smartwatch with sleep tracking and alerts."),
    ("smartphones", "Voltix Nova 12 (8/256GB)", 32990, 35990, 12, 23, ["photo-1511707171634-5f897ff02aa9", "photo-1640936343842-268f9d87e764"], "6.7-inch 120Hz display, 50MP camera and 5000mAh battery."),
    ("smartphones", "Voltix Nova Lite (6/128GB)", 18990, 20990, 20, 46, ["photo-1480694313141-fce5e697ee25"], "Big battery, smooth 90Hz screen and a clean, fast Android."),
    ("smartphones", "Voltix Mini 5G (8/128GB)", 24990, None, 9, 12, ["photo-1634403665443-81dc4d75843a"], "Compact 5G phone that fits comfortably in one hand."),
    ("laptops", "Voltix Book Air 14", 74990, 79990, 8, 15, ["photo-1496181133206-80ce9b88a853", "photo-1541807084-5c52b6b3adef"], "Thin 14-inch laptop, 16GB RAM, 512GB SSD, all-day battery."),
    ("laptops", "Voltix Book Pro 16", 124990, 134990, 5, 7, ["photo-1525547719571-a2d4ac8945e2"], "16-inch creator laptop with a bright display and fast graphics."),
    ("laptops", "Voltix Book 15 Student", 52990, 56990, 11, 21, ["photo-1588872657578-7efd1f1555ed"], "Reliable 15.6-inch laptop for classes, office work and Netflix."),
    ("speakers", "Voltix Boom Go Portable Speaker", 3490, 3990, 38, 95, ["photo-1608043152269-423dbba4e7e1", "photo-1767796427185-ada2bf414860"], "IP67 waterproof Bluetooth speaker with punchy 20W sound."),
    ("speakers", "Voltix Party Max Speaker", 9990, 11990, 10, 26, ["photo-1589256469067-ea99122bbdc4"], "Loud 60W party speaker with lights and a 12-hour battery."),
    ("speakers", "Voltix Home Smart Speaker", 6990, None, 16, 18, ["photo-1529359744902-86b2ab9edaea"], "Room-filling smart speaker for music, radio and podcasts."),
    ("cameras", "Voltix Snap DSLR Kit 18-55mm", 54990, 59990, 6, 9, ["photo-1516035069371-29a1b244cc32", "photo-1502982720700-bfff97f2ecac"], "24MP DSLR with an 18-55mm lens, perfect first camera."),
    ("cameras", "Voltix Zoom Bridge Camera", 34990, 37990, 7, 11, ["photo-1452780212940-6f5c0d14d848"], "40x optical zoom bridge camera for travel and wildlife."),
    ("cameras", "Voltix Pro Mirrorless Body", 89990, None, 3, 4, ["photo-1564466809058-bf4114d55352"], "Full-frame-feel mirrorless body with fast autofocus and 4K video."),
    ("gaming", "Voltix Strike Wireless Controller", 4490, 4990, 28, 73, ["photo-1612287230202-1ff1d85d1bdf", "photo-1592840496694-26d035b52b48"], "Low-latency wireless controller for PC, Android and console."),
    ("gaming", "Voltix Arctic Controller", 4790, None, 19, 32, ["photo-1768166206522-dfffc0783b0c"], "Limited white edition with textured grips and dual rumble."),
    ("gaming", "Voltix Pro Gamepad", 3290, 3790, 33, 58, ["photo-1600861194942-f883de0dfe96"], "Comfortable wired gamepad with a 3-metre braided cable."),
    ("keyboards", "Voltix K87 Mechanical Keyboard", 5990, 6990, 21, 49, ["photo-1618384887929-16ec33fab9ef", "photo-1547394765-185e1e68f34e"], "Hot-swappable switches, RGB backlight and a tenkeyless layout."),
    ("keyboards", "Voltix Pastel Mechanical Keyboard", 6490, None, 12, 22, ["photo-1760708551859-6559dc9640b8"], "Teal and coral keycaps, smooth linear switches, wireless."),
    ("keyboards", "Voltix Classic Mechanical Keyboard", 4490, 4990, 26, 38, ["photo-1595044426077-d36d9236d54a"], "Full-size board with satisfying tactile keys and a metal plate."),
    ("power", "Voltix PowerCell 20000mAh", 2490, 2990, 70, 160, ["photo-1760708825913-65a50b3dc39b"], "Charges a phone 4 times, 22.5W fast charging, three outputs."),
    ("power", "Voltix Slim Power Bank 10000mAh", 1490, 1790, 85, 205, ["photo-1566554738544-d962991c3fee"], "Slim pocket power bank with USB-C in and out."),
    ("power", "Voltix Braided USB-C Cable (2m)", 490, 650, 200, 310, ["photo-1585995603413-eb35b5f4a50b"], "Tough braided cable, 60W fast charging and fast data."),
]

FEATURES = [
    {"title": "Official warranty", "description": "6 to 12 months brand warranty on every gadget.", "icon": "shield-check"},
    {"title": "7-day easy returns", "description": "Changed your mind? Return it within 7 days.", "icon": "rotate-ccw"},
    {"title": "Cash on delivery", "description": "Pay when it reaches you, anywhere in Bangladesh.", "icon": "truck"},
]

LOCATIONS = [
    {"name": "Inside Dhaka", "charge_cents": 6000},
    {"name": "Outside Dhaka", "charge_cents": 12000},
]


def slugify(text: str) -> str:
    out = "".join(c.lower() if c.isalnum() else "-" for c in text)
    while "--" in out:
        out = out.replace("--", "-")
    return out.strip("-")


def theme(cat_ids: list[str], featured: list[str], showcase: str, event_ids: list[str]) -> dict:
    return {
        "siteName": NAME,
        "tagline": "Gadgets you'll love, prices you'll like",
        "logoType": "text",
        "logoImage": "",
        "primaryColor": "#2563EB",
        "accentColor": "#0F172A",
        "surfaceColor": "#F5F7FB",
        "displayFont": "plus-jakarta-sans",
        "bodyFont": "inter",
        "buttonStyle": "Pill",
        "announcementItems": [
            "Free delivery on orders over ৳5,000",
            "11.11 Tech Sale: up to 15% off headphones, watches and more",
            "Official warranty on every gadget, cash on delivery all over Bangladesh",
        ],
        "announcementDivider": "•",
        "navLinks": [
            {"id": "n1", "path": "/shop", "label": "Shop"},
            {"id": "n2", "path": "/categories", "label": "Categories"},
            {"id": "n3", "path": "/about", "label": "About"},
            {"id": "n4", "path": "/contact", "label": "Contact"},
        ],
        "headerButtons": [{"id": "b1", "label": "Cart", "style": "primary"}],
        "pages": [
            {"id": "p1", "path": "/", "type": "home", "title": "Home", "enabled": True},
            {"id": "p2", "path": "/shop", "type": "products", "title": "Shop", "enabled": True},
            {"id": "p3", "path": "/shop/:slug", "type": "productDetail", "title": "Product details", "enabled": True},
            {"id": "p4", "path": "/categories", "type": "categories", "title": "Categories", "enabled": True},
            {"id": "p5", "path": "/cart", "type": "cart", "title": "Cart", "enabled": True},
            {"id": "p6", "path": "/checkout", "type": "checkout", "title": "Checkout", "enabled": True},
            {"id": "p7", "path": "/about", "type": "about", "title": "About", "enabled": True},
            {"id": "p8", "path": "/contact", "type": "contact", "title": "Contact", "enabled": True},
            {"id": "p9", "path": "/faq", "type": "faq", "title": "FAQ", "enabled": True},
            {"id": "p10", "path": "/privacy", "type": "privacy", "title": "Privacy", "enabled": True},
            {"id": "p11", "path": "/terms", "type": "terms", "title": "Terms", "enabled": True},
        ],
        "sections": [
            {"id": "s1", "type": "banner"},
            {"id": "s2", "type": "hero"},
            {"id": "s3", "type": "categories"},
            {"id": "s4", "type": "featureProducts"},
            {"id": "s4e", "type": "events"},
            {"id": "s5", "type": "productShowcase"},
            {"id": "s5b", "type": "categoryShowcase"},
            {"id": "s6", "type": "whyChooseUs"},
            {"id": "s7", "type": "features"},
            {"id": "s9", "type": "bannerCta"},
            {"id": "s8", "type": "testimonials"},
            {"id": "s10", "type": "footer"},
        ],
        "heroMediaType": "image",
        "heroVideo": "",
        "heroImages": [photo("photo-1587831990711-23ca6441447b", 1600, 700)],
        "heroImagesSquare": [
            photo("photo-1437419764061-2473afe69fc2", 900, 900),
            photo("photo-1546435770-a3e426bf472b", 900, 900),
        ],
        "categoriesTitle": "Shop by category",
        "selectedCategoryIds": cat_ids,
        "featureProductsTitle": "Trending gadgets",
        "selectedProductIds": featured,
        "selectedEventIds": event_ids,
        "showcaseProductId": showcase,
        "categoryShowcaseTitle": "Explore the range",
        "categoryShowcaseCategoryIds": cat_ids[:5],
        "whyTitle": "Why shop with Voltix",
        "whyImage": photo("photo-1499951360447-b19be8fe80f5", 1000, 800),
        "why1Title": "Genuine products only",
        "why1": "Every gadget comes from authorised importers with an official warranty card, so you never get a copy.",
        "why2Title": "Tested before it ships",
        "why2": "We power on and check each phone, laptop and camera before packing, so it works the moment it arrives.",
        "why3Title": "Real help after you buy",
        "why3": "Questions about setup or warranty? Our team answers on WhatsApp every day from 10 AM to 10 PM.",
        "featuresTitle": "Shopping made easy",
        "feature1Title": "Fast delivery",
        "feature1": "Inside Dhaka in 24 hours, the rest of Bangladesh in 2 to 3 days.",
        "feature1IconKind": "icon",
        "feature1Icon": "solar:delivery-bold",
        "feature1Image": "",
        "feature2Title": "Official warranty",
        "feature2": "6 to 12 months brand warranty, claimed through us, no running around.",
        "feature2IconKind": "icon",
        "feature2Icon": "solar:shield-check-bold",
        "feature2Image": "",
        "feature3Title": "Cash on delivery",
        "feature3": "Check your gadget at the door and pay the rider. bKash and Nagad accepted too.",
        "feature3IconKind": "icon",
        "feature3Icon": "solar:wallet-money-bold",
        "feature3Image": "",
        "ctaTitle": "The 11.11 Tech Sale is on",
        "ctaBody": "Up to 15% off headphones, smartwatches, speakers and more, for a limited time.",
        "ctaButton": "Shop the sale",
        "testimonialsTitle": "What our customers say",
        "testimonialsMode": "cards",
        "testimonials": [
            {"id": "t1", "name": "Rafsan Ahmed", "role": "Dhaka", "rating": 5, "image": "",
             "quote": "Got the Aura headphones next day in Mirpur. Noise cancelling is excellent and the warranty card was in the box."},
            {"id": "t2", "name": "Nusrat Jahan", "role": "Chattogram", "rating": 5, "image": "",
             "quote": "Ordered a smartwatch with cash on delivery, checked it with the rider, paid and done. Very smooth."},
            {"id": "t3", "name": "Tanvir Hasan", "role": "Sylhet", "rating": 4, "image": "",
             "quote": "Laptop came well packed and already tested. Support helped me set it up on WhatsApp."},
        ],
        "testimonialsCards": [],
        "testimonialsScreenshots": [],
        "footerDescription": "Voltix brings genuine gadgets to Bangladesh: headphones, smartwatches, phones, laptops, cameras and gaming gear, with official warranty and cash on delivery nationwide.",
        "footerShopLabel": "Shop",
        "footerShopLinks": [
            {"id": "fs1", "path": "/shop", "label": "All products"},
            {"id": "fs2", "path": "/categories", "label": "Categories"},
        ],
        "footerCompanyLabel": "Company",
        "footerCompanyLinks": [
            {"id": "fc1", "path": "/about", "label": "About"},
            {"id": "fc2", "path": "/contact", "label": "Contact"},
            {"id": "fc3", "path": "/faq", "label": "FAQ"},
        ],
    }


BUSINESS = {
    "name": NAME,
    "type": "electronics",
    "email": "hello@voltix.demo",
    "phone": "01700000000",
    "whatsapp": "01700000000",
    "address": {"street": "Level 4, Bashundhara City", "region": "Panthapath", "city": "Dhaka",
                "postal_code": "1215", "country": "Bangladesh"},
    "socials": {"facebook": "https://facebook.com/", "instagram": "https://instagram.com/", "youtube": "https://youtube.com/"},
    "opening_hours": ["Sa-Th 10:00 AM-10:00 PM"],
    "support_note": "This is a SoftuneBD demo store. Orders placed here are not shipped.",
}

ABOUT = {
    "image": photo("photo-1575318634028-6a0cfcb60c59", 1200, 800),
    "heading": "Genuine gadgets, honest prices",
    "paragraphs": [
        "Voltix started with a simple idea: buying electronics in Bangladesh should be easy and safe. No copies, no hidden charges, no guessing about warranty.",
        "We work with authorised importers, test every device before it ships, and stay with you after the sale. Pay with cash on delivery, bKash or Nagad, and get help on WhatsApp whenever you need it.",
        "This is a demo store built on SoftuneBD to show what your own shop can look like.",
    ],
}

FAQS = [
    {"question": "Do your products come with warranty?", "answer": "Yes. Every product has an official 6 to 12 month warranty. Keep the warranty card that comes in the box."},
    {"question": "How long does delivery take?", "answer": "Inside Dhaka within 24 hours, outside Dhaka in 2 to 3 working days."},
    {"question": "Can I pay cash on delivery?", "answer": "Yes, cash on delivery is available all over Bangladesh. You can also pay with bKash or Nagad."},
    {"question": "What if the product is faulty?", "answer": "Tell us within 7 days and we replace it free of charge after a quick check."},
]

LEGAL = {
    "terms": {"title": "Terms of Service", "published": True, "content": "By placing an order on Voltix you agree to provide correct delivery details and to receive and pay for the order. Prices and stock can change without notice. Warranty is provided as stated on each product. This is a SoftuneBD demo store; orders are not processed."},
    "privacy": {"title": "Privacy Policy", "published": True, "content": "We collect only what we need to deliver your order: your name, phone number and address. We never sell your information. This is a SoftuneBD demo store."},
}


async def main() -> None:
    async with SessionLocal() as db:
        if (await db.execute(select(Site.id).where(Site.subdomain == SUBDOMAIN))).scalar_one_or_none():
            print(f"{SUBDOMAIN} already exists, nothing changed.")
            return

        user, site = await create_tenant_owner_and_site(
            db,
            email=EMAIL,
            password=secrets.token_urlsafe(24),
            workspace_name=NAME,
            plan="business",
            template_key="bazaar",
            site_name=NAME,
            subdomain=SUBDOMAIN,
            full_name="Voltix Demo",
        )
        site = await db.get(Site, site.id)
        tenant_id, site_id = site.tenant_id, site.id

        cats: dict[str, Category] = {}
        for i, (slug, name, desc, icon, pid) in enumerate(CATEGORIES):
            c = Category(tenant_id=tenant_id, site_id=site_id, name=name, slug=slug, description=desc,
                         icon=icon, image_url=photo(pid, 800, 800), banner_url=photo(pid, 1600, 500),
                         sort_order=i, is_active=True)
            db.add(c)
            cats[slug] = c
        await db.flush()

        products: list[Product] = []
        for i, (cslug, name, price, compare, stock, sold, pids, short) in enumerate(PRODUCTS):
            p = Product(
                tenant_id=tenant_id, site_id=site_id, category_id=cats[cslug].id,
                sku=f"VX-{1001 + i}", name=name, slug=slugify(name),
                short_description=short,
                description=f"{short} Comes with an official warranty card and a 7-day easy return. Delivered all over Bangladesh with cash on delivery.",
                price_cents=price * 100, compare_at_cents=compare * 100 if compare else None,
                cost_price_cents=int(price * 100 * 0.78), currency="BDT",
                stock=stock, track_stock=True, initial_sold_count=sold,
                images=[{"url": photo(pid)} for pid in pids],
                attributes={}, is_active=True,
                free_delivery=False, delivery_charges=LOCATIONS, features=FEATURES,
            )
            db.add(p)
            products.append(p)
        await db.flush()

        by_cat = {slug: [p for p, row in zip(products, PRODUCTS) if row[0] == slug] for slug in cats}
        sale = Event(tenant_id=tenant_id, site_id=site_id, name="11.11 Tech Sale", slug="11-11-tech-sale",
                     description="Up to 15% off headphones, smartwatches and speakers.",
                     image_url=photo("photo-1468495244123-6c6c332eeece", 1600, 700),
                     cta_label="Shop the sale", discount_percent=15, is_active=True, is_popup=False)
        sale.products = by_cat["headphones"] + by_cat["smartwatches"] + by_cat["speakers"]
        gaming = Event(tenant_id=tenant_id, site_id=site_id, name="Gaming Week", slug="gaming-week",
                       description="10% off controllers and keyboards this week.",
                       image_url=photo("photo-1552820728-8b83bb6b773f", 1600, 700),
                       cta_label="Level up", discount_percent=10, is_active=True, is_popup=False)
        gaming.products = by_cat["gaming"] + by_cat["keyboards"]
        db.add_all([sale, gaming])
        await db.flush()

        featured = [str(p.id) for p in (by_cat["headphones"][:1] + by_cat["earbuds"][:1] + by_cat["smartwatches"][:1]
                    + by_cat["smartphones"][:1] + by_cat["laptops"][:1] + by_cat["speakers"][:1]
                    + by_cat["cameras"][:1] + by_cat["gaming"][:1])]
        site.theme = {**(site.theme or {}), **theme([str(c.id) for c in cats.values()], featured,
                                                     str(by_cat["headphones"][0].id), [str(sale.id), str(gaming.id)])}
        site.business = {**(site.business or {}), **BUSINESS}
        site.about = ABOUT
        site.faqs = FAQS
        site.legal = LEGAL
        site.shipping = {"locations": LOCATIONS, "free_over_cents": 500000}
        site.seo = {**(site.seo or {}), "keywords": "gadgets Bangladesh, headphones, smartwatch, earbuds, laptop, power bank, cash on delivery",
                    "sitemap_enabled": True, "noindex": False}
        db.add(PaymentConnection(tenant_id=tenant_id, site_id=site_id, provider="cod",
                                 label="Cash on Delivery", status="connected", config={}))

        # Publish, the same steps app/api/trial.py takes for a new store.
        pages = (await db.execute(select(SitePage).where(SitePage.site_id == site_id))).scalars().all()
        for page in pages:
            page.is_published = True
        site.status = "published"
        site.published_at = datetime.now(UTC)
        await db.commit()

        await cache.invalidate_site(site.subdomain, site.custom_domain)
        await queue.publish(queue.JOB_REVALIDATE_SITE, {"site_id": str(site_id), "paths": ["/"]})
        await queue.publish(queue.JOB_GENERATE_SITEMAP, {"site_id": str(site_id)})
        await queue.publish(queue.JOB_ATTACH_DOMAIN, {"site_id": str(site_id)})
        await queue.publish(queue.JOB_CAPTURE_SCREENSHOT, {"site_id": str(site_id)})

        print(f"Voltix demo store created: https://{SUBDOMAIN}.softunebd.com")
        print(f"  {len(CATEGORIES)} categories, {len(PRODUCTS)} products, 2 sale events")


if __name__ == "__main__":
    asyncio.run(main())
