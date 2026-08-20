"""
seed_aurora_orders_6mo.py — one-off script, NOT a migration.

Re-seeds ONLY the aurora-owner (RIVELLE) site's orders, spanning the last
6 calendar months instead of 2 — so the dashboard's Sales Analysis chart
(bucketRevenueByMonth, last 6 calendar months) has real data in all 6 bars
instead of 4 empty ones. Leaves categories/products/theme/site settings
untouched.

Run once: venv/Scripts/python.exe scripts/seed_aurora_orders_6mo.py
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

random.seed(7)


def new_id() -> str:
    return str(uuid.uuid4())


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

# Weighted toward fulfilled/paid so revenue/order-count trends look like a
# genuinely operating store, not a pile of pending orders.
STATUS_WEIGHTS = [
    ("fulfilled", 55), ("paid", 20), ("pending", 10), ("cancelled", 10), ("refunded", 5),
]
STATUSES = [s for s, w in STATUS_WEIGHTS for _ in range(w)]

DAYS_SPAN = 182  # ~6 calendar months
ORDER_COUNT = 390  # same ~2.17 orders/day density as the original 2-month seed


async def main() -> None:
    conn = await asyncpg.connect(os.environ["DATABASE_URL"].replace("+asyncpg", ""), statement_cache_size=0)

    async with conn.transaction():
        await conn.execute("DELETE FROM order_items WHERE order_id IN (SELECT id FROM orders WHERE site_id = $1)", SITE_ID)
        await conn.execute("DELETE FROM orders WHERE site_id = $1", SITE_ID)

        prod_rows = await conn.fetch(
            "SELECT id, name, sku, price_cents FROM products WHERE site_id = $1", SITE_ID
        )
        if not prod_rows:
            raise SystemExit("No products found for this site — run seed_aurora_demo.py first.")
        product_ids = [str(r["id"]) for r in prod_rows]
        prod_by_id = {str(r["id"]): r for r in prod_rows}

        now = datetime.now(timezone.utc)
        order_num = 1000

        for _ in range(ORDER_COUNT):
            order_num += 1
            days_ago = random.uniform(0, DAYS_SPAN)
            created_at = now - timedelta(days=days_ago, hours=random.uniform(0, 23))
            status = random.choice(STATUSES)

            first = random.choice(BD_FIRST_NAMES)
            last = random.choice(BD_LAST_NAMES)
            city, region, postal = random.choice(BD_AREAS)
            phone = f"+8801{random.randint(3,9)}{random.randint(10000000, 99999999)}"
            location = "Inside Dhaka" if city == "Dhaka" else "Outside Dhaka"
            shipping_cents = 8000 if location == "Inside Dhaka" else 14000

            item_count = random.randint(1, 3)
            chosen = random.sample(product_ids, item_count)
            subtotal = 0
            items = []
            for pid in chosen:
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
    print(f"Done. Seeded {ORDER_COUNT} orders over the last {DAYS_SPAN} days.")


if __name__ == "__main__":
    asyncio.run(main())
