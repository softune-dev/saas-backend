"""The searchable knowledge of one store, built from plain dicts.

`build_index` takes plain data (not ORM rows) on purpose: the live service
feeds it from the database, and the offline evaluation script feeds it from a
store's public API, and both exercise the exact same code.

What goes in the index is TEXT and IDS only. Prices and stock are deliberately
not copied here: a cached "in stock" that is an hour stale is a lie told to a
customer, so the engine reads those live for the one or two products an
answer is actually about (see service.py). That also means a price edit never
needs an index rebuild.
"""

import math
import re
from typing import Any

from app.assistant.text import content_tokens, tokens

INDEX_VERSION = 1
MAX_ENTRY_ANSWER = 900

# A store with fewer written answers AND fewer products than this has not told
# the assistant enough to be trusted with open questions yet.
_READY_TEXT_ENTRIES = 3
_READY_PRODUCTS = 5


def _clip(text: str, n: int = MAX_ENTRY_ANSWER) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _messenger(facebook: str) -> str:
    """m.me link from a Facebook page URL, or '' when there isn't a page name."""
    m = re.search(r"facebook\.com/([A-Za-z0-9.\-_]+)", facebook or "")
    if not m or m.group(1).lower() in {"profile.php", "pages", "people", "share"}:
        return ""
    return f"https://m.me/{m.group(1)}"


def _entry(eid: str, kind: str, q: str, a: str) -> dict[str, Any]:
    qt = tokens(q)
    return {"id": eid, "kind": kind, "q": q, "a": _clip(a), "t": qt, "at": tokens(a)}


def build_index(data: dict[str, Any]) -> dict[str, Any]:
    """`data` keys (all optional): name, business, about, faqs, shipping,
    payment_methods, categories, products, events, qa."""
    entries: list[dict[str, Any]] = []

    # Merchant-written answers first: the merchant's own words always win.
    for i, row in enumerate(data.get("qa") or []):
        q, a = str(row.get("q", "")).strip(), str(row.get("a", "")).strip()
        if q and a:
            entries.append(_entry(f"qa{i}", "custom", q, a))
    for i, row in enumerate(data.get("faqs") or []):
        q, a = str(row.get("question", "")).strip(), str(row.get("answer", "")).strip()
        if q and a:
            entries.append(_entry(f"faq{i}", "faq", q, a))

    about = data.get("about") or {}
    paragraphs = [p for p in (about.get("paragraphs") or []) if p]
    if paragraphs:
        text = " ".join(paragraphs[:2])
        entries.append(_entry("about", "about", "about us who are you what do you sell story", text))

    business = data.get("business") or {}
    socials = business.get("socials") or {}
    addr = business.get("address") or {}
    address = ", ".join(str(addr[k]) for k in ("street", "region", "city") if addr.get(k))
    contact = {
        "phone": business.get("phone") or "",
        "whatsapp": business.get("whatsapp") or "",
        "email": business.get("email") or "",
        "address": address,
        "messenger": _messenger(socials.get("facebook", "")),
        "instagram": socials.get("instagram") or "",
        "facebook": socials.get("facebook") or "",
    }

    products = []
    for p in data.get("products") or []:
        name_t = content_tokens(tokens(p.get("name", "")))
        cat_t = content_tokens(tokens(p.get("category_name") or ""))
        extra_t = content_tokens(tokens(p.get("short", "")))
        if not name_t:
            continue
        products.append({
            "id": str(p["id"]), "slug": p.get("slug", ""), "name": p.get("name", ""),
            "cat": p.get("category_name") or "", "nt": name_t, "ct": cat_t, "xt": extra_t,
        })

    categories = [
        {"id": str(c["id"]), "slug": c.get("slug", ""), "name": c.get("name", ""),
         "t": content_tokens(tokens(c.get("name", "")))}
        for c in data.get("categories") or []
        if c.get("name")
    ]

    # Delivery charges are per product; the ones used most are the store's
    # de facto rates, which is what a "delivery charge?" question wants.
    charge_count: dict[tuple[str, int], int] = {}
    for p in data.get("products") or []:
        for dc in p.get("delivery_charges") or []:
            if dc.get("name"):
                key = (dc["name"], int(dc.get("charge_cents") or 0))
                charge_count[key] = charge_count.get(key, 0) + 1
    charges = [{"name": n, "charge_cents": c} for (n, c), _ in sorted(charge_count.items(), key=lambda kv: -kv[1])][:4]

    shipping = data.get("shipping") or {}
    free_over = shipping.get("free_over_cents") or data.get("free_delivery_over_cents")

    # Document frequency over every entry question, for IDF weighting.
    df: dict[str, int] = {}
    for e in entries:
        for t in set(e["t"]):
            df[t] = df.get(t, 0) + 1
    n = max(len(entries), 1)
    idf = {t: round(math.log((n + 1) / (d + 0.5)) + 1, 3) for t, d in df.items()}
    idf_unseen = round(math.log((n + 1) / 0.5) + 1, 3)

    text_entries = [e for e in entries if e["kind"] in ("custom", "faq")]
    ready = len(text_entries) >= _READY_TEXT_ENTRIES or len(products) >= _READY_PRODUCTS

    return {
        "v": INDEX_VERSION,
        "name": data.get("name") or business.get("name") or "",
        "entries": entries,
        "idf": idf,
        "idf_unseen": idf_unseen,
        "products": products,
        "categories": categories,
        "events": [
            {"name": e.get("name", ""), "pct": int(e.get("discount_percent") or 0)}
            for e in data.get("events") or []
            if e.get("name")
        ],
        "payments": [m.get("label") or m.get("provider", "") for m in data.get("payment_methods") or []],
        "delivery": {"free_over_cents": free_over, "charges": charges},
        "contact": contact,
        "ready": ready,
        "counts": {"text": len(text_entries), "products": len(products)},
    }
