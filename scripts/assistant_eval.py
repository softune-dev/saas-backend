"""Offline check of the store assistant against real demo stores.

Reads each store's PUBLIC API (the same data any visitor can see), builds the
index with the same code the live endpoint uses, and asks it a fixed set of
questions in English, Banglish and Bangla. No database, no writes.

    venv\\Scripts\\python.exe scripts\\assistant_eval.py            # all stores, summary
    venv\\Scripts\\python.exe scripts\\assistant_eval.py raaj -v    # one store, every answer

The expected kind for each question is written next to it: a "fallback" for
"do you sell cars" is a PASS, an answer for it would be the bug.
"""

import asyncio
import json
import sys
import urllib.request

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")

from app.assistant.engine import respond  # noqa: E402
from app.assistant.index import build_index  # noqa: E402

API = "https://api.softunebd.com/public/site/"
STORES = {
    "raaj": "raaj.softunebd.com", "tinytots": "tinytots.softunebd.com", "voltix": "voltix.softunebd.com",
    "pawpal": "pawpal.softunebd.com", "ghorkona": "ghorkona.softunebd.com", "freshkart": "freshkart.softunebd.com",
    "stride": "stride.softunebd.com", "noor": "noormodest.softunebd.com", "swarna": "swarnajewels.softunebd.com",
    "oudh": "oudhhouse.softunebd.com",
}


def get(url: str):
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)


def load(host: str) -> dict:
    cfg = get(API + host)["site"]
    cats = get(f"{API}{host}/categories")
    prods = get(f"{API}{host}/products?limit=100")["items"]
    events = get(f"{API}{host}/events")
    return {"cfg": cfg, "cats": cats, "prods": prods, "events": events}


def to_data(raw: dict) -> dict:
    cfg = raw["cfg"]
    return {
        "name": cfg["name"], "business": cfg.get("business"), "about": cfg.get("about"), "faqs": cfg.get("faqs"),
        "shipping": {"free_over_cents": cfg.get("free_delivery_over_cents")},
        "payment_methods": cfg.get("payment_methods"),
        "categories": [{"id": c["id"], "slug": c["slug"], "name": c["name"]} for c in raw["cats"]],
        "events": [{"name": e["name"], "discount_percent": e.get("discountPercent", 0)} for e in raw["events"]],
        "products": [
            {"id": p["id"], "slug": p["slug"], "name": p["name"], "short": p.get("shortDescription", ""),
             "category_name": p.get("categoryName"),
             "delivery_charges": [{"name": d["name"], "charge_cents": round(d["charge"] * 100)} for d in p.get("deliveryCharges", [])]}
            for p in raw["prods"]
        ],
    }


def live_loader(raw: dict):
    by_id = {}
    for p in raw["prods"]:
        variants = [{"type": v["type"], "values": [x["value"] for x in v["values"]]} for v in (p.get("attributes") or {}).get("variants", [])]
        by_id[p["id"]] = {
            "id": p["id"], "slug": p["slug"], "name": p["name"], "image": (p.get("images") or [""])[0],
            "price_cents": round(p["price"] * 100),
            "compare_at_cents": round(p["compareAtPrice"] * 100) if p.get("compareAtPrice") else None,
            "in_stock": p.get("inStock", True), "variants": variants,
            "delivery_charges": [{"name": d["name"], "charge_cents": round(d["charge"] * 100)} for d in p.get("deliveryCharges", [])],
        }

    async def loader(ids):
        return {i: by_id[i] for i in ids if i in by_id}
    return loader


def questions(raw: dict) -> list[tuple[str, str]]:
    p0 = raw["prods"][0]
    p1 = raw["prods"][6] if len(raw["prods"]) > 6 else raw["prods"][-1]
    cat = raw["cats"][1]["name"]
    return [
        (f"how much is {p0['name']}", "product"), (f"{p0['name']} dam koto", "product"),
        (f"is {p1['name']} in stock", "product"), (f"{p1['name']} er price", "product"),
        (f"do you have {cat}", "category"),
        ("delivery charge koto", "delivery"), ("what is the delivery charge", "delivery"),
        ("how long does delivery take", "faq"), ("delivery koto din lagbe", "faq"),
        ("can I pay with bkash", "any"), ("what payment methods do you accept", "payment"),
        ("can I return or exchange", "faq"), ("return kora jabe?", "faq"),
        ("any discount or offer right now", "discount"), ("whatsapp number", "contact"),
        ("where is your shop", "contact"), ("hello", "smalltalk"), ("thanks", "smalltalk"),
        ("do you sell cars", "fallback"), ("who is the president of france", "fallback"),
        # Written after the matcher was tuned: not seen while fixing it.
        (f"ei {p0['name']} ta ki available?", "product"), (f"{p1['name']} koto taka", "product"),
        ("free delivery ache?", "delivery"), ("cash on delivery hoy?", "payment"),
        (f"what colors does {p0['name']} come in", "product"), ("tell me about your shop", "about"),
        ("do you deliver outside dhaka", "delivery"), ("can I get my money back", "fallback"),
        ("আপনাদের ডেলিভারি চার্জ কত", "delivery"), ("রিটার্ন করা যাবে?", "faq"),
        ("পেমেন্ট কিভাবে করব", "payment"), ("কোন অফার আছে?", "discount"),
    ]


async def run(name: str, verbose: bool) -> tuple[int, int]:
    raw = load(STORES[name])
    index = build_index(to_data(raw))
    live = live_loader(raw)
    ok = total = 0
    for q, want in questions(raw):
        r = await respond(index, q, live, store_name=raw["cfg"]["name"])
        good = (
            (want == "any" and r["kind"] != "fallback")
            or r["kind"] == want
            or (want == "faq" and r["kind"] in ("faq", "custom"))
            or (want == "category" and r["kind"] in ("category", "product"))
            or (want == "payment" and r["kind"] in ("payment", "faq"))
            or (want == "delivery" and r["kind"] in ("delivery", "faq"))
        )
        ok += good
        total += 1
        if verbose or not good:
            print(f"  {'ok ' if good else 'BAD'} [{r['kind']:<9}] {q}  ->  {r['answer'][:90]}")
    print(f"{name}: {ok}/{total}  (ready={index['ready']}, entries={index['counts']})")
    return ok, total


async def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    verbose = "-v" in sys.argv
    names = args or list(STORES)
    ok = total = 0
    for n in names:
        a, b = await run(n, verbose)
        ok, total = ok + a, total + b
    print(f"TOTAL {ok}/{total}")


if __name__ == "__main__":
    asyncio.run(main())
