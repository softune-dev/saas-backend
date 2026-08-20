"""
seed_aurora_demo.py — one-off script, NOT a migration.

Wipes and repopulates ALL content for the aurora-owner tenant's site with a
complete, realistic fashion-brand demo: categories, products, theme/section
content, site settings (SEO/shipping/contact/FAQ/legal), and 2 months of
fake historical orders. Used to make the Aurora template a convincing
marketing showcase ("RIVELLE" brand) rather than empty scaffolding.

Scoped to ONE site_id only — never touches other tenants (e.g. bazaar-owner).
Images are deliberately left blank; uploaded manually afterward in the
dashboard media library.

Run once: venv/Scripts/python.exe scripts/seed_aurora_demo.py
"""

import asyncio
import json
import os
import random
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg
from dotenv import load_dotenv

load_dotenv()

SITE_ID = "00e518c1-ea24-4a60-9219-6dcb4c7f6ca3"
TENANT_ID = "6a48f81b-0dc0-4935-93c4-06968e3960c1"

random.seed(42)


def new_id() -> str:
    return str(uuid.uuid4())


def slugify(text: str) -> str:
    return "-".join(text.lower().replace("&", "and").replace("'", "").split())


# =============================================================================
#  Categories
# =============================================================================

CATEGORIES = [
    {
        "slug": "dresses",
        "name": "Dresses",
        "description": "Effortless silhouettes for every occasion, from everyday casual to evening elegance.",
    },
    {
        "slug": "shirts-tops",
        "name": "Shirts & Tops",
        "description": "Elevated basics and statement tops, tailored for a modern, versatile wardrobe.",
    },
    {
        "slug": "outerwear",
        "name": "Outerwear",
        "description": "Jackets and coats built for Dhaka's changing seasons — sharp, warm, and durable.",
    },
    {
        "slug": "denim",
        "name": "Denim",
        "description": "Premium denim cut for comfort and shape, from classic straight to modern relaxed fits.",
    },
    {
        "slug": "footwear",
        "name": "Footwear",
        "description": "Everyday shoes and sandals that pair comfort with clean, modern design.",
    },
    {
        "slug": "accessories",
        "name": "Accessories",
        "description": "The finishing touches — bags, belts, and jewelry to complete every look.",
    },
]

SIZE_SETS = {
    "dresses": ["S", "M", "L", "XL"],
    "shirts-tops": ["S", "M", "L", "XL"],
    "outerwear": ["S", "M", "L", "XL", "XXL"],
    "denim": ["28", "30", "32", "34", "36"],
    "footwear": ["38", "39", "40", "41", "42", "43"],
    "accessories": None,  # one-size accessories, color only
}

COLOR_POOL = [
    "Black", "White", "Beige", "Navy", "Olive", "Rust",
    "Charcoal", "Cream", "Grey", "Camel",
]

# =============================================================================
#  Products — (name, price_taka, compare_taka|None, short_description,
#              description, [feature (title, body), ...])
# =============================================================================

PRODUCTS = {
    "dresses": [
        (
            "Aria Wrap Midi Dress", 1890, 2400,
            "Elegant wrap-style midi dress in soft crepe with a flattering waist tie.",
            "The Aria Midi is cut from a fluid crepe fabric that drapes beautifully without clinging. "
            "A self-tie waist sash lets you adjust the fit through the day, while the wrap-front silhouette "
            "flatters every body type. Finished with a soft V-neckline and elbow-length sleeves, it moves "
            "seamlessly from desk to dinner.",
            [
                ("Fabric", "100% crepe, lightly textured for a matte finish that resists wrinkling."),
                ("Fit", "True to size with an adjustable wrap waist for a customizable fit."),
                ("Care", "Hand wash cold or dry clean; do not tumble dry."),
            ],
        ),
        (
            "Noor Linen Shirt Dress", 2150, None,
            "Breathable linen shirt dress with a button-front closure, perfect for warm days.",
            "Noor is built for comfort without sacrificing structure — a relaxed linen shirt dress with "
            "a full button placket, collared neckline, and a self-fabric belt to cinch the waist. Roomy "
            "patch pockets and rolled-tab sleeves round out a piece designed for long, warm afternoons.",
            [
                ("Fabric", "100% breathable linen, pre-washed for softness."),
                ("Fit", "Relaxed, oversized fit — size down for a fitted look."),
                ("Details", "Belted waist, patch pockets, rolled-tab sleeves."),
            ],
        ),
        (
            "Elle Puff-Sleeve Mini Dress", 1650, 2100,
            "Playful mini dress with statement puff sleeves and a fitted bodice.",
            "Elle brings a touch of drama to everyday dressing with voluminous puff sleeves and a "
            "fitted, smocked bodice that transitions into a flared mini skirt. A hidden back zip keeps "
            "the silhouette clean, and the lightweight poly-viscose blend keeps it comfortable for all-day wear.",
            [
                ("Fabric", "Poly-viscose blend with a soft, brushed hand feel."),
                ("Fit", "Fitted bodice, flared skirt — true to size."),
                ("Closure", "Concealed back-zip closure."),
            ],
        ),
        (
            "Sana Satin Slip Dress", 2450, None,
            "Bias-cut satin slip dress with delicate adjustable straps, made for evening occasions.",
            "Cut on the bias for a fluid, body-skimming drape, Sana is an evening staple finished in "
            "a lustrous satin. Adjustable spaghetti straps and a cowl neckline add a refined, minimal "
            "elegance that pairs equally well with heels or flats.",
            [
                ("Fabric", "Premium satin with a subtle sheen."),
                ("Fit", "Bias-cut, body-skimming — true to size."),
                ("Details", "Adjustable straps, cowl neckline, side slit."),
            ],
        ),
        (
            "Iris Floral Maxi Dress", 2890, 3400,
            "Flowing floral maxi dress with a tiered skirt and elasticated waist.",
            "Iris is an easy, romantic maxi in an original floral print, built with a tiered skirt for "
            "movement and an elasticated waist for all-day comfort. Short flutter sleeves and a V-neckline "
            "complete a dress made for long summer days and golden-hour dinners.",
            [
                ("Fabric", "Lightweight viscose with an original floral print."),
                ("Fit", "Elasticated waist, relaxed through the body — true to size."),
                ("Details", "Tiered skirt, flutter sleeves, fully lined."),
            ],
        ),
    ],
    "shirts-tops": [
        (
            "Milo Oxford Button-Down", 1290, None,
            "Classic Oxford cotton shirt with a tailored fit, perfect for work or weekend.",
            "A wardrobe staple done right — Milo is woven from breathable Oxford cotton with a tailored, "
            "not-too-slim fit, a button-down collar, and a single chest pocket. Reinforced stitching at "
            "every seam means it holds its shape wash after wash.",
            [
                ("Fabric", "100% Oxford cotton, mid-weight."),
                ("Fit", "Tailored fit — true to size."),
                ("Details", "Button-down collar, single chest pocket, curved hem."),
            ],
        ),
        (
            "Vega Ribbed Turtleneck", 1150, None,
            "Soft ribbed-knit turtleneck top, a versatile layering essential.",
            "Vega is a fine-gauge ribbed turtleneck designed to layer under blazers and jackets or worn "
            "alone with denim. The stretch knit moves with you, and the fitted turtleneck collar keeps "
            "things polished without feeling restrictive.",
            [
                ("Fabric", "Ribbed cotton-elastane blend with 4-way stretch."),
                ("Fit", "Fitted through the body — true to size."),
                ("Care", "Machine wash cold, lay flat to dry."),
            ],
        ),
        (
            "Kai Relaxed Linen Shirt", 1450, 1800,
            "Lightweight linen shirt with a relaxed fit and dropped shoulders.",
            "Kai is cut generously with dropped shoulders and a boxy body for an easy, off-duty silhouette. "
            "The breathable linen weave keeps things cool, while a curved hem means it looks just as good "
            "tucked in or left loose over denim.",
            [
                ("Fabric", "100% linen, garment-washed for softness."),
                ("Fit", "Relaxed, boxy fit with dropped shoulders."),
                ("Details", "Curved hem, single chest pocket."),
            ],
        ),
        (
            "Nova Silk-Blend Blouse", 1990, None,
            "Elegant silk-blend blouse with a fluid drape and mother-of-pearl buttons.",
            "Nova is finished in a fluid silk-cotton blend that drapes beautifully without ever feeling "
            "delicate. Mother-of-pearl buttons and a soft point collar bring a refined finish to a top "
            "built for the office and beyond.",
            [
                ("Fabric", "Silk-cotton blend with a soft, fluid drape."),
                ("Fit", "Semi-fitted — true to size."),
                ("Details", "Mother-of-pearl buttons, soft point collar."),
            ],
        ),
        (
            "Theo Graphic Crew Tee", 790, None,
            "Premium heavyweight cotton tee with a minimalist front print.",
            "Theo is built from heavyweight combed cotton for a structured drape that won't go sheer or "
            "shrink out of shape. A minimalist front graphic and a ribbed crew neckline keep it clean "
            "and easy to style with anything.",
            [
                ("Fabric", "220 GSM heavyweight combed cotton."),
                ("Fit", "Regular fit — true to size."),
                ("Print", "Water-based, cracks-resistant screen print."),
            ],
        ),
    ],
    "outerwear": [
        (
            "Signature Trench Coat", 4290, 5200,
            "Water-resistant cotton twill trench coat with a belted waist and classic collar.",
            "Our Signature Trench is tailored from a water-resistant cotton twill in a timeless double-breasted "
            "silhouette. A belted waist, storm flap, and classic notched collar make this the one coat that "
            "carries a wardrobe through every season.",
            [
                ("Fabric", "Water-resistant cotton twill with a smooth finish."),
                ("Fit", "Tailored, double-breasted — true to size."),
                ("Details", "Belted waist, storm flap, notched collar, welt pockets."),
            ],
        ),
        (
            "Aspen Quilted Puffer Jacket", 3650, None,
            "Lightweight quilted puffer with a packable design, built for cold city nights.",
            "Aspen packs serious warmth into a surprisingly lightweight shell. Diamond-quilted panels "
            "trap heat efficiently, an elasticated hem and cuffs seal out the cold, and the whole jacket "
            "packs down into its own pocket for travel.",
            [
                ("Fabric", "Recycled polyester shell with synthetic down fill."),
                ("Fit", "Regular fit — true to size."),
                ("Details", "Packable design, zip pockets, elasticated hem."),
            ],
        ),
        (
            "Rey Denim Trucker Jacket", 2450, None,
            "Classic denim trucker jacket with a slightly oversized fit.",
            "A rework of the archetypal trucker, Rey is cut in mid-weight rigid denim with a slightly "
            "oversized body for a modern, relaxed fit. Twin chest pockets and button cuffs keep the "
            "classic details that make this jacket a lifelong staple.",
            [
                ("Fabric", "Mid-weight 100% cotton denim."),
                ("Fit", "Slightly oversized — size down for a classic fit."),
                ("Details", "Twin chest pockets, button cuffs, adjustable side tabs."),
            ],
        ),
        (
            "Wren Wool-Blend Overcoat", 5490, 6500,
            "Tailored wool-blend overcoat with a single-breasted silhouette.",
            "Wren is a considered investment piece — a wool-blend overcoat with a clean single-breasted "
            "front, notched lapel, and a fully lined interior. Structured shoulders and a knee-skimming "
            "length make it a natural layer over tailoring or knitwear alike.",
            [
                ("Fabric", "Wool-polyester blend, fully lined."),
                ("Fit", "Tailored, structured shoulders — true to size."),
                ("Details", "Single-breasted, notched lapel, interior pocket."),
            ],
        ),
        (
            "Cove Utility Bomber Jacket", 2990, None,
            "Cropped utility bomber with multiple pockets and ribbed cuffs.",
            "Cove takes the classic bomber silhouette and adds function — multiple utility pockets, a "
            "cropped length, and ribbed hem and cuffs for a snug, secure fit. Lightweight enough for "
            "layering, structured enough to wear alone.",
            [
                ("Fabric", "Cotton-nylon blend with a matte finish."),
                ("Fit", "Cropped, regular fit — true to size."),
                ("Details", "Utility pockets, ribbed hem and cuffs, zip closure."),
            ],
        ),
    ],
    "denim": [
        (
            "Mira High-Rise Straight Jeans", 1890, None,
            "High-rise straight-leg jeans in a rigid, non-stretch denim for a clean silhouette.",
            "Mira is cut from rigid, non-stretch denim for a straight leg that holds its shape all day. "
            "A high-rise waist and clean five-pocket styling make this the foundational pair every "
            "denim rotation needs.",
            [
                ("Fabric", "100% rigid cotton denim, mid-weight."),
                ("Fit", "High-rise, straight leg — true to size."),
                ("Wash", "Mid-blue wash with minimal fading."),
            ],
        ),
        (
            "Dash Relaxed Fit Jeans", 1990, None,
            "Relaxed-fit jeans with a mid-rise waist and tapered leg.",
            "Dash trades the skinny fit for room to move — a relaxed cut through the thigh that tapers "
            "gently at the ankle. The mid-rise waist and soft-washed denim make this an easy everyday pair.",
            [
                ("Fabric", "Cotton denim with 2% elastane for comfort stretch."),
                ("Fit", "Relaxed through the thigh, tapered leg — true to size."),
                ("Wash", "Soft indigo wash."),
            ],
        ),
        (
            "Cruz Skinny Stretch Jeans", 1750, 2200,
            "Stretch-denim skinny jeans that hold their shape all day.",
            "Cruz is built from a high-recovery stretch denim that moves with you and bounces right back "
            "into shape. A mid-rise waist and slim taper down to the ankle make this the go-to pair for "
            "everyday wear.",
            [
                ("Fabric", "Cotton denim with 3% elastane, high-recovery stretch."),
                ("Fit", "Skinny, mid-rise — true to size."),
                ("Wash", "Dark indigo, minimal distressing."),
            ],
        ),
        (
            "Ives Wide-Leg Denim", 2150, None,
            "Wide-leg jeans with a high-rise waist for a statement silhouette.",
            "Ives leans into a fuller, wide-leg silhouette with a high-rise waist that elongates the "
            "frame. Crafted from a heavier-weight denim that holds structure through the leg for a "
            "clean drape from waist to hem.",
            [
                ("Fabric", "Heavyweight 100% cotton denim."),
                ("Fit", "Wide-leg, high-rise — true to size."),
                ("Wash", "Light stone wash."),
            ],
        ),
        (
            "Pax Distressed Boyfriend Jeans", 2050, None,
            "Relaxed boyfriend-fit jeans with subtle distressing at the knee.",
            "Pax borrows from menswear tailoring with a loose, boyfriend fit and a slightly dropped "
            "crotch. Subtle whiskering and knee distressing add lived-in character without going "
            "overboard.",
            [
                ("Fabric", "Cotton denim with a soft, broken-in hand feel."),
                ("Fit", "Relaxed boyfriend fit — size down for a closer fit."),
                ("Details", "Subtle distressing at the knee, raw hem."),
            ],
        ),
    ],
    "footwear": [
        (
            "Clea Leather Ankle Boots", 3450, 4200,
            "Genuine leather ankle boots with a block heel and side zip.",
            "Clea pairs genuine leather uppers with a stable block heel and an inside zip for easy on "
            "and off. A round toe and cushioned footbed keep this boot comfortable enough for full days "
            "on your feet.",
            [
                ("Material", "Genuine leather upper, rubber outsole."),
                ("Fit", "True to size, medium width."),
                ("Details", "Inside zip closure, 5cm block heel."),
            ],
        ),
        (
            "Rove Canvas Low-Top Sneakers", 1650, None,
            "Everyday canvas sneakers with a cushioned insole and rubber sole.",
            "Rove is a clean, minimal low-top sneaker in durable canvas, built on a cushioned EVA "
            "midsole and a grippy rubber outsole. Designed to go with everything from denim to dresses.",
            [
                ("Material", "Durable canvas upper, EVA midsole."),
                ("Fit", "True to size."),
                ("Details", "Lace-up closure, reinforced toe cap."),
            ],
        ),
        (
            "Solis Strappy Block Heels", 2290, None,
            "Strappy block heel sandals, comfortable enough for all-day wear.",
            "Solis balances height and comfort with a stable 6cm block heel and adjustable ankle strap. "
            "A cushioned footbed and soft strap lining mean you can wear these from morning meetings "
            "into the evening.",
            [
                ("Material", "Faux leather straps, cushioned footbed."),
                ("Fit", "True to size, adjustable ankle strap."),
                ("Heel", "6cm stable block heel."),
            ],
        ),
        (
            "Terra Suede Loafers", 2690, None,
            "Classic suede loafers with a cushioned footbed and durable sole.",
            "Terra brings a timeless penny-loafer silhouette in soft suede, finished with a cushioned "
            "footbed and a flexible rubber sole built for all-day comfort without sacrificing polish.",
            [
                ("Material", "Genuine suede upper, rubber outsole."),
                ("Fit", "True to size, medium width."),
                ("Details", "Cushioned footbed, stacked heel."),
            ],
        ),
        (
            "Marsh Slide Sandals", 990, None,
            "Minimalist slide sandals in soft molded rubber, perfect for everyday comfort.",
            "Marsh keeps it simple — a single-strap slide molded from soft, lightweight rubber with a "
            "contoured footbed that supports the arch. Quick-drying and easy to clean, built for daily wear.",
            [
                ("Material", "Molded EVA rubber, water-resistant."),
                ("Fit", "True to size."),
                ("Details", "Contoured, arch-supportive footbed."),
            ],
        ),
    ],
    "accessories": [
        (
            "Nell Structured Tote Bag", 2350, None,
            "Structured vegan-leather tote with an interior laptop sleeve.",
            "Nell is built for daily carry — a structured vegan-leather tote with a padded interior "
            "laptop sleeve, zip-top closure, and dual carry handles. Roomy enough for a full workday, "
            "polished enough for anywhere after.",
            [
                ("Material", "Vegan leather exterior, cotton twill lining."),
                ("Capacity", "Fits up to a 15-inch laptop."),
                ("Details", "Zip-top closure, interior zip pocket."),
            ],
        ),
        (
            "Orin Leather Belt", 790, None,
            "Full-grain leather belt with a brushed brass buckle.",
            "Orin is cut from full-grain leather that develops character with wear, finished with a "
            "brushed brass buckle. A wardrobe basic built to outlast trends.",
            [
                ("Material", "Full-grain leather, brushed brass hardware."),
                ("Width", "3.5cm width, fits standard belt loops."),
                ("Sizing", "Trim to fit — sizing guide on product page."),
            ],
        ),
        (
            "Fable Layered Necklace Set", 650, None,
            "Set of three delicate layered necklaces in gold-tone finish.",
            "Fable is a set of three necklaces in varying lengths, designed to be worn stacked or "
            "separately. Finished in a tarnish-resistant gold-tone plating for everyday wear.",
            [
                ("Material", "Gold-tone plated brass, tarnish-resistant."),
                ("Set includes", "3 necklaces at 40cm, 45cm, and 50cm."),
                ("Care", "Avoid contact with water and perfume."),
            ],
        ),
        (
            "Dune Canvas Crossbody Bag", 1190, None,
            "Compact canvas crossbody bag with adjustable strap and zip pockets.",
            "Dune is a compact, everyday crossbody in durable canvas with a leather-trimmed adjustable "
            "strap and two zip pockets for the essentials — phone, cards, keys, done.",
            [
                ("Material", "Durable canvas with leather trim."),
                ("Strap", "Adjustable, up to 60cm drop."),
                ("Details", "Two zip pockets, magnetic front flap."),
            ],
        ),
        (
            "Halo Oversized Sunglasses", 890, None,
            "UV-protective oversized sunglasses with an acetate frame.",
            "Halo pairs an oversized, face-flattering silhouette with full UV400 protection. The acetate "
            "frame is lightweight enough for all-day wear, with spring hinges for a comfortable, secure fit.",
            [
                ("Lens", "UV400 protection, polarized."),
                ("Frame", "Lightweight acetate with spring hinges."),
                ("Includes", "Protective case and cleaning cloth."),
            ],
        ),
    ],
}

SHIPPING_LOCATIONS = [
    {"id": "loc-inside-dhaka", "name": "Inside Dhaka", "charge_cents": 8000},
    {"id": "loc-outside-dhaka", "name": "Outside Dhaka", "charge_cents": 14000},
]

BD_FIRST_NAMES = [
    "Kamrul", "Nusrat", "Tanvir", "Farzana", "Rakibul", "Sadia", "Imran", "Tasnim",
    "Ashraful", "Mim", "Shafin", "Nabila", "Rifat", "Jarin", "Sabbir", "Anika",
    "Mehedi", "Tahsin", "Fahim", "Priya",
]
BD_LAST_NAMES = [
    "Hasan", "Islam", "Ahmed", "Rahman", "Chowdhury", "Karim", "Hossain", "Akter",
    "Alam", "Khan", "Uddin", "Begum", "Siddique", "Sarkar",
]
BD_AREAS = [
    ("Mirpur 2", "Dhaka", "1216"),
    ("Dhanmondi", "Dhaka", "1209"),
    ("Banani", "Dhaka", "1213"),
    ("Uttara Sector 7", "Dhaka", "1230"),
    ("Bashundhara R/A", "Dhaka", "1229"),
    ("Chattogram GEC", "Chattogram", "4000"),
    ("Sylhet Zindabazar", "Sylhet", "3100"),
    ("Rajshahi Shaheb Bazar", "Rajshahi", "6100"),
    ("Khulna Sonadanga", "Khulna", "9100"),
    ("Gazipur Tongi", "Gazipur", "1710"),
]


async def main() -> None:
    conn = await asyncpg.connect(os.environ["DATABASE_URL"].replace("+asyncpg", ""), statement_cache_size=0)

    async with conn.transaction():
        # ---------------------------------------------------------------
        # 1. Wipe existing content for THIS site only
        # ---------------------------------------------------------------
        await conn.execute("DELETE FROM order_items WHERE order_id IN (SELECT id FROM orders WHERE site_id = $1)", SITE_ID)
        await conn.execute("DELETE FROM orders WHERE site_id = $1", SITE_ID)
        await conn.execute("DELETE FROM products WHERE site_id = $1", SITE_ID)
        await conn.execute("DELETE FROM categories WHERE site_id = $1", SITE_ID)
        await conn.execute("UPDATE order_counters SET next_number = 1000 WHERE site_id = $1", SITE_ID)

        # ---------------------------------------------------------------
        # 2. Categories
        # ---------------------------------------------------------------
        category_ids: dict[str, str] = {}
        for i, cat in enumerate(CATEGORIES):
            cid = new_id()
            category_ids[cat["slug"]] = cid
            await conn.execute(
                """
                INSERT INTO categories
                    (id, tenant_id, site_id, parent_id, name, slug, description,
                     image_url, banner_url, icon, sort_order, is_active)
                VALUES ($1, $2, $3, NULL, $4, $5, $6, NULL, NULL, NULL, $7, TRUE)
                """,
                cid, TENANT_ID, SITE_ID, cat["name"], cat["slug"], cat["description"], i,
            )

        # ---------------------------------------------------------------
        # 3. Products
        # ---------------------------------------------------------------
        product_ids: dict[str, list[str]] = {}
        all_product_ids: list[str] = []
        sku_counter = 1
        for cat_slug, items in PRODUCTS.items():
            product_ids[cat_slug] = []
            sizes = SIZE_SETS[cat_slug]
            for name, price, compare, short_desc, desc, features in items:
                pid = new_id()
                slug = slugify(name)
                colors = random.sample(COLOR_POOL, 3)
                variants = [
                    {
                        "type": "Color",
                        "affectsPrice": False,
                        "values": [{"value": c} for c in colors],
                    }
                ]
                if sizes:
                    variants.insert(0, {
                        "type": "Size",
                        "affectsPrice": False,
                        "values": [{"value": s} for s in sizes],
                    })
                free_delivery = sku_counter % 2 == 0
                sku = f"RIV-{cat_slug[:3].upper()}-{sku_counter:03d}"
                stock = random.randint(15, 120)
                initial_sold = random.randint(8, 340)
                feature_objs = [{"title": t, "description": d} for t, d in features]

                await conn.execute(
                    """
                    INSERT INTO products
                        (id, tenant_id, site_id, category_id, sku, name, slug,
                         description, short_description, price_cents, compare_at_cents,
                         currency, stock, track_stock, images, attributes, is_active,
                         video_url, serial_number, unit, initial_sold_count,
                         free_delivery, delivery_charge_cents, delivery_charges, features)
                    VALUES
                        ($1, $2, $3, $4, $5, $6, $7,
                         $8, $9, $10, $11,
                         'BDT', $12, TRUE, '[]'::jsonb, $13::jsonb, TRUE,
                         NULL, NULL, 'pcs', $14,
                         $15, NULL, $16::jsonb, $17::jsonb)
                    """,
                    pid, TENANT_ID, SITE_ID, category_ids[cat_slug], sku, name, slug,
                    desc, short_desc, price * 100, (compare * 100) if compare else None,
                    stock,
                    json.dumps({"variants": variants}),
                    initial_sold,
                    free_delivery,
                    json.dumps([] if free_delivery else SHIPPING_LOCATIONS),
                    json.dumps(feature_objs),
                )
                product_ids[cat_slug].append(pid)
                all_product_ids.append(pid)
                sku_counter += 1

        # ---------------------------------------------------------------
        # 4. Theme (Aurora contract) + site settings
        # ---------------------------------------------------------------
        row = await conn.fetchrow("SELECT theme FROM sites WHERE id = $1", SITE_ID)
        theme = json.loads(row["theme"])

        featured_ids = [product_ids[c][0] for c in ["dresses", "outerwear", "denim", "footwear", "shirts-tops", "accessories"]]
        showcase_id = product_ids["outerwear"][0]  # Signature Trench Coat

        theme.update({
            "siteName": "RIVELLE",
            "tagline": "Modern essentials for everyday elegance",
            "primaryColor": "#B08968",
            "accentColor": "#171717",
            "surfaceColor": "#F7F5F2",
            "displayFont": "newsreader",
            "bodyFont": "figtree",
            "buttonStyle": "Pill",
            "logoType": "text",
            "logoImage": "",
            "heroImages": [],
            "heroImagesSquare": [],
            "announcementItems": [
                "New Season Arrivals — Shop the Autumn Edit",
                "Free Delivery Inside Dhaka on Orders Over ৳2,000",
                "Members Get Early Access to New Drops — Join Free",
            ],
            "announcementDivider": "✦",
            "categoriesTitle": "Shop by Category",
            "selectedCategoryIds": list(category_ids.values()),
            "featureProductsTitle": "Trending Now",
            "selectedProductIds": featured_ids,
            "showcaseTitle": "The Signature Trench",
            "showcaseBody": "Tailored from water-resistant cotton twill, our signature trench is designed to "
                            "move with you through every season — a wardrobe staple built to last.",
            "showcaseCta": "Shop the Trench",
            "showcaseProductId": showcase_id,
            "categoryShowcaseTitle": "Shop by Collection",
            "categoryShowcaseCategoryIds": list(category_ids.values()),
            "whyTitle": "Why Shop RIVELLE",
            "whyImage": "",
            "why1Title": "Premium Fabrics",
            "why1": "Every piece is cut from thoughtfully sourced, high-quality fabrics built to last season after season.",
            "why2Title": "Designed in Small Batches",
            "why2": "We release limited runs so you get considered design, not fast-fashion overproduction.",
            "why3Title": "7-Day Easy Exchange",
            "why3": "Didn't get the fit right? Exchange any unworn item within 7 days, hassle-free.",
            "featuresTitle": "Our Commitments",
            "feature1Title": "Sustainably Sourced",
            "feature1": "We partner with certified mills that meet strict environmental and labor standards.",
            "feature1Icon": "leaf",
            "feature1IconKind": "icon",
            "feature1Image": "",
            "feature2Title": "Quality Guaranteed",
            "feature2": "Every item is quality-checked twice before it leaves our warehouse.",
            "feature2Icon": "shield-check",
            "feature2IconKind": "icon",
            "feature2Image": "",
            "feature3Title": "Fast, Tracked Delivery",
            "feature3": "Orders ship within 24 hours with real-time tracking across Bangladesh.",
            "feature3Icon": "package",
            "feature3IconKind": "icon",
            "feature3Image": "",
            "testimonialsTitle": "Loved by Our Customers",
            "testimonials": [
                {"id": "t1", "name": "Nusrat Jahan", "role": "Verified Buyer", "rating": 5, "image": "",
                 "quote": "RIVELLE's dresses fit like they were tailored just for me. The fabric quality is unmatched at this price point."},
                {"id": "t2", "name": "Tanvir Ahmed", "role": "Verified Buyer", "rating": 5, "image": "",
                 "quote": "Ordered the trench coat and it exceeded expectations — sharp fit, great stitching, fast delivery."},
                {"id": "t3", "name": "Farzana Islam", "role": "Verified Buyer", "rating": 5, "image": "",
                 "quote": "I've bought from RIVELLE three times now. Consistent quality and their customer service is excellent."},
                {"id": "t4", "name": "Rakibul Hasan", "role": "Verified Buyer", "rating": 4, "image": "",
                 "quote": "The denim jacket is now my go-to. True to size and the color hasn't faded after multiple washes."},
                {"id": "t5", "name": "Sadia Rahman", "role": "Verified Buyer", "rating": 5, "image": "",
                 "quote": "Beautiful packaging, beautiful clothes. RIVELLE has become my favorite place to shop online."},
            ],
            "ctaTitle": "Ready to Refresh Your Wardrobe?",
            "ctaBody": "Explore our latest collection of everyday essentials, crafted for comfort and made to last.",
            "ctaButton": "Shop New Arrivals",
            "footerDescription": "RIVELLE is a modern fashion label crafting considered essentials for everyday "
                                  "elegance — designed in small batches, made to last.",
            "footerShopLabel": "Shop",
            "footerShopLinks": [{"id": "fs1", "path": "/shop", "label": "All Products"}],
            "footerCompanyLabel": "Company",
            "footerCompanyLinks": [
                {"id": "fc1", "path": "/about", "label": "About"},
                {"id": "fc2", "path": "/contact", "label": "Contact"},
                {"id": "fc3", "path": "/faq", "label": "FAQ"},
            ],
        })

        business = {
            "name": "RIVELLE",
            "type": "Fashion & Apparel",
            "description": "RIVELLE is a modern fashion label based in Dhaka, offering considered everyday "
                            "essentials for men and women — from tailored outerwear to elevated basics.",
            "phone": "+8801711223344",
            "whatsapp": "+8801711223344",
            "email": "hello@rivelle.com",
            "logo_url": "",
            "address": {
                "street": "House 24, Road 11, Banani",
                "city": "Dhaka",
                "region": "Dhaka Division",
                "postal_code": "1213",
                "country": "Bangladesh",
            },
            "map_url": "",
            "hours": [
                {"day": "Saturday", "open": "10:00", "close": "20:00", "closed": False},
                {"day": "Sunday", "open": "10:00", "close": "20:00", "closed": False},
                {"day": "Monday", "open": "10:00", "close": "20:00", "closed": False},
                {"day": "Tuesday", "open": "10:00", "close": "20:00", "closed": False},
                {"day": "Wednesday", "open": "10:00", "close": "20:00", "closed": False},
                {"day": "Thursday", "open": "10:00", "close": "20:00", "closed": False},
                {"day": "Friday", "open": "", "close": "", "closed": True},
            ],
            "opening_hours": [
                "Saturday 10:00-20:00", "Sunday 10:00-20:00", "Monday 10:00-20:00",
                "Tuesday 10:00-20:00", "Wednesday 10:00-20:00", "Thursday 10:00-20:00",
                "Friday Closed",
            ],
            "socials": {
                "facebook": "https://facebook.com/rivelle.bd",
                "instagram": "https://instagram.com/rivelle.bd",
                "tiktok": "",
                "youtube": "",
                "x": "",
                "linkedin": "",
                "whatsapp": "https://wa.me/8801711223344",
                "telegram": "",
                "other": "",
            },
            "support_note": "Our support team typically responds within 2 hours during business hours.",
        }

        seo = {
            "title_suffix": "| RIVELLE — Modern Fashion",
            "meta_description": "Shop RIVELLE's curated collection of modern fashion essentials — dresses, "
                                 "outerwear, denim, footwear and accessories. Free delivery inside Dhaka.",
            "keywords": "fashion, clothing, dresses, outerwear, denim, footwear, accessories, Bangladesh fashion, online clothing store",
            "og_title": "RIVELLE — Modern Fashion Essentials",
            "og_description": "Considered, elevated basics designed in small batches. Shop the new collection.",
            "og_image": "",
            "favicon": "",
            "noindex": False,
            "sitemap_enabled": True,
            "google_analytics": "",
            "google_search_console": "",
            "facebook_pixel": "",
        }

        shipping = {"locations": SHIPPING_LOCATIONS}

        faqs = [
            {"id": "faq1", "question": "What sizes do you carry?",
             "answer": "We carry sizes XS to XXL across most categories. Check each product's size guide for exact measurements."},
            {"id": "faq2", "question": "How long does delivery take?",
             "answer": "Orders inside Dhaka arrive within 1-2 business days. Outside Dhaka typically takes 3-5 business days."},
            {"id": "faq3", "question": "What is your return/exchange policy?",
             "answer": "We offer free exchanges within 7 days of delivery for unworn items with tags attached."},
            {"id": "faq4", "question": "Do you offer Cash on Delivery?",
             "answer": "Yes, Cash on Delivery is available nationwide alongside online payment options."},
            {"id": "faq5", "question": "How do I track my order?",
             "answer": "Once your order ships, you'll receive a tracking link via SMS and email."},
            {"id": "faq6", "question": "Are your products true to size?",
             "answer": "Yes, most items run true to size. We recommend checking the size chart on each product page."},
            {"id": "faq7", "question": "Can I cancel my order after placing it?",
             "answer": "Orders can be cancelled within 1 hour of placement by contacting our support team."},
            {"id": "faq8", "question": "Do you ship internationally?",
             "answer": "Currently we only ship within Bangladesh. International shipping is coming soon."},
            {"id": "faq9", "question": "How do I care for my garments?",
             "answer": "Care instructions are listed on the label of each item — most pieces are machine washable on a gentle cycle."},
            {"id": "faq10", "question": "How can I contact customer support?",
             "answer": "Reach us via WhatsApp, email at hello@rivelle.com, or the contact form on our website — we usually respond within 2 hours."},
        ]

        legal = {
            "privacy": {
                "title": "Privacy Policy",
                "published": True,
                "content": (
                    "RIVELLE (\"we\", \"us\") respects your privacy. This policy explains what information we "
                    "collect when you shop with us and how we use it.\n\n"
                    "Information We Collect: When you place an order, we collect your name, phone number, "
                    "delivery address, and order details. We do not store payment card information — that is "
                    "handled securely by our payment partners.\n\n"
                    "How We Use It: We use your information to process and deliver orders, respond to support "
                    "requests, and — with your consent — send updates about new collections and offers.\n\n"
                    "Data Sharing: We share order details only with the courier partner fulfilling your delivery. "
                    "We do not sell your personal information to third parties.\n\n"
                    "Your Rights: You may request a copy of your data or ask us to delete your account information "
                    "at any time by contacting hello@rivelle.com."
                ),
            },
            "terms": {
                "title": "Terms & Conditions",
                "published": True,
                "content": (
                    "By placing an order with RIVELLE, you agree to the following terms.\n\n"
                    "Orders & Payment: All prices are listed in Bangladeshi Taka (৳) and include applicable taxes. "
                    "We accept Cash on Delivery and online payments through our checkout partners.\n\n"
                    "Shipping: Orders inside Dhaka are delivered within 1-2 business days; outside Dhaka within "
                    "3-5 business days. Delivery charges are calculated at checkout based on your location.\n\n"
                    "Returns & Exchanges: Unworn items with original tags may be exchanged within 7 days of "
                    "delivery. Sale items are final and not eligible for exchange unless faulty.\n\n"
                    "Order Cancellation: Orders may be cancelled within 1 hour of placement by contacting our "
                    "support team before the order is processed for dispatch.\n\n"
                    "Product Accuracy: We make every effort to display product colors and details accurately; "
                    "slight variations may occur due to screen settings."
                ),
            },
        }

        await conn.execute(
            """
            UPDATE sites
            SET theme = $2::jsonb, business = $3::jsonb, seo = $4::jsonb,
                shipping = $5::jsonb, faqs = $6::jsonb, legal = $7::jsonb,
                name = 'RIVELLE'
            WHERE id = $1
            """,
            SITE_ID,
            json.dumps(theme), json.dumps(business), json.dumps(seo),
            json.dumps(shipping), json.dumps(faqs), json.dumps(legal),
        )

        # ---------------------------------------------------------------
        # 5. Fake historical orders (last ~60 days)
        # ---------------------------------------------------------------
        # Distribution weighted toward fulfilled/paid so the demo looks like
        # a genuinely operating store rather than a pile of pending orders.
        STATUS_WEIGHTS = [
            ("fulfilled", 55), ("paid", 20), ("pending", 10), ("cancelled", 10), ("refunded", 5),
        ]
        statuses = [s for s, w in STATUS_WEIGHTS for _ in range(w)]

        now = datetime.now(timezone.utc)
        order_num = 1000
        products_flat = [(pid, cat) for cat, ids in product_ids.items() for pid in ids]

        # Look up name/price per product id for snapshotting order items.
        prod_rows = await conn.fetch(
            "SELECT id, name, sku, price_cents FROM products WHERE site_id = $1", SITE_ID
        )
        prod_by_id = {str(r["id"]): r for r in prod_rows}

        order_count = 130
        for _ in range(order_count):
            order_num += 1
            days_ago = random.uniform(0, 60)
            created_at = now - timedelta(days=days_ago, hours=random.uniform(0, 23))
            status = random.choice(statuses)

            first = random.choice(BD_FIRST_NAMES)
            last = random.choice(BD_LAST_NAMES)
            city, region, postal = random.choice(BD_AREAS)
            phone = f"+8801{random.randint(3,9)}{random.randint(10000000, 99999999)}"
            location = "Inside Dhaka" if city == "Dhaka" or "Dhaka" in region else "Outside Dhaka"
            shipping_cents = 8000 if location == "Inside Dhaka" else 14000

            item_count = random.randint(1, 3)
            chosen = random.sample(products_flat, item_count)
            subtotal = 0
            items = []
            for pid, _cat in chosen:
                p = prod_by_id[pid]
                qty = random.randint(1, 2)
                unit_price = p["price_cents"]
                line_total = unit_price * qty
                subtotal += line_total
                items.append((pid, p["name"], p["sku"], unit_price, qty, line_total))

            total = subtotal + shipping_cents
            order_id = new_id()

            await conn.execute(
                """
                INSERT INTO orders
                    (id, tenant_id, site_id, order_number, customer, status,
                     subtotal_cents, shipping_cents, tax_cents, total_cents,
                     currency, notes, meta, created_at, updated_at)
                VALUES
                    ($1, $2, $3, $4, $5::jsonb, $6,
                     $7, $8, 0, $9,
                     'BDT', NULL, $10::jsonb, $11, $11)
                """,
                order_id, TENANT_ID, SITE_ID, f"ORD-{order_num}",
                json.dumps({
                    "first_name": first, "last_name": last,
                    "phone": phone, "address": f"{city}, {region}, Bangladesh",
                    "city": city, "zip": postal,
                }),
                status,
                subtotal, shipping_cents, total,
                json.dumps({
                    "payment_method": random.choice(["cod", "cod", "cod", "bkash"]),
                    "transaction_id": None,
                    "delivery_location": location,
                }),
                created_at,
            )

            for pid, name, sku, unit_price, qty, line_total in items:
                await conn.execute(
                    """
                    INSERT INTO order_items
                        (id, order_id, product_id, name_snapshot, sku_snapshot,
                         unit_price_cents, quantity, total_cents, created_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    """,
                    new_id(), order_id, pid, name, sku, unit_price, qty, line_total, created_at,
                )

        await conn.execute("UPDATE order_counters SET next_number = $2 WHERE site_id = $1", SITE_ID, order_num + 1)

    await conn.close()
    print(f"Done. Seeded {len(CATEGORIES)} categories, {sum(len(v) for v in PRODUCTS.values())} products, {order_count} orders.")


if __name__ == "__main__":
    asyncio.run(main())
