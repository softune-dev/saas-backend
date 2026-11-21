"""Turning a decision into the reply the customer sees, in English or Bangla.

Money is integer cents everywhere (CLAUDE.md rule 7); the only place cents
become a "৳1,290" string is `taka` below, with integer division, never float.
Each reply is a plain dict: the text, what it was based on, and any buttons
(product links, contact channels) so the widget needs no logic of its own.
"""

import re
from typing import Any


def taka(cents: int | None) -> str:
    cents = int(cents or 0)
    whole, rest = divmod(cents, 100)
    return f"৳{whole:,}" + (f".{rest:02d}" if rest else "")


def wa_link(number: str) -> str:
    digits = re.sub(r"\D", "", number or "")
    if not digits:
        return ""
    if digits.startswith("0"):
        digits = "88" + digits
    return f"https://wa.me/{digits}"


def contact_channels(index: dict[str, Any]) -> dict[str, str]:
    c = index.get("contact") or {}
    out = {
        "whatsapp": wa_link(c.get("whatsapp") or c.get("phone") or ""),
        "phone": f"tel:{re.sub(r'[^0-9+]', '', c['phone'])}" if c.get("phone") else "",
        "email": f"mailto:{c['email']}" if c.get("email") else "",
        "messenger": c.get("messenger") or "",
        "instagram": c.get("instagram") or "",
    }
    return {k: v for k, v in out.items() if v}


def _out(text: str, kind: str, source: str, *, lang: str, **extra: Any) -> dict[str, Any]:
    return {
        "answer": text, "kind": kind, "source": source, "lang": lang,
        "answered": kind != "fallback", "fallback": extra.pop("fallback", None),
        "products": extra.pop("products", []), "actions": extra.pop("actions", []),
        "contact": extra.pop("contact", {}), "followups": extra.pop("followups", []),
    }


_FOLLOW = {
    "en": ["Delivery charge?", "Which payments do you accept?", "Any offers right now?"],
    "bn": ["ডেলিভারি চার্জ কত?", "কোন পেমেন্ট নেওয়া যায়?", "এখন কোনো অফার আছে?"],
}


def smalltalk(kind: str, lang: str, name: str, index: dict[str, Any]) -> dict[str, Any]:
    who = name or ("us" if lang == "en" else "আমাদের")
    if kind == "thanks":
        text = "You're welcome! Anything else I can help with?" if lang == "en" else "আপনাকে ধন্যবাদ! আর কিছু জানতে চান?"
    elif lang == "en":
        text = f"Hi! I'm the {who} assistant. Ask me about products, prices, delivery or payments."
    else:
        text = f"হ্যালো! আমি {who}-এর অ্যাসিস্ট্যান্ট। প্রোডাক্ট, দাম, ডেলিভারি বা পেমেন্ট নিয়ে জিজ্ঞেস করুন।"
    return _out(text, "smalltalk", "", lang=lang, followups=_FOLLOW[lang])


def entry(e: dict[str, Any], lang: str, index: dict[str, Any]) -> dict[str, Any]:
    source = {"custom": "Store answer", "faq": "Store FAQ", "about": "About us"}.get(e["kind"], "Store info")
    if lang == "bn":
        source = {"custom": "স্টোরের উত্তর", "faq": "স্টোর FAQ", "about": "আমাদের সম্পর্কে"}.get(e["kind"], "স্টোর তথ্য")
    return _out(e["a"], e["kind"], source, lang=lang, followups=_FOLLOW[lang])


def _options(p: dict[str, Any], lang: str) -> str:
    parts = []
    for v in (p.get("variants") or [])[:3]:
        vals = ", ".join(str(x) for x in v.get("values", [])[:6])
        if vals:
            parts.append(f"{v['type']}: {vals}")
    if not parts:
        return ""
    return (" Available in " if lang == "en" else " পাওয়া যায় ") + "; ".join(parts) + "."


def _card(p: dict[str, Any]) -> dict[str, Any]:
    return {k: p.get(k) for k in ("id", "slug", "name", "image", "price_cents", "compare_at_cents", "in_stock")}


def products(items: list[dict[str, Any]], intents: set[str], lang: str, index: dict[str, Any]) -> dict[str, Any]:
    en = lang == "en"
    if len(items) == 1:
        p = items[0]
        price = taka(p["price_cents"])
        was = taka(p["compare_at_cents"]) if p.get("compare_at_cents") and p["compare_at_cents"] > p["price_cents"] else ""
        if en:
            text = f"{p['name']} is {price}" + (f" (was {was})" if was else "") + ". "
            text += "It's in stock." if p.get("in_stock") else "It's out of stock right now."
        else:
            text = f"{p['name']} এর দাম {price}" + (f" (আগে {was})" if was else "") + "। "
            text += "স্টকে আছে।" if p.get("in_stock") else "এখন স্টকে নেই।"
        text += _options(p, lang)
        if "delivery" in intents and p.get("delivery_charges"):
            ch = ", ".join(f"{d['name']} {taka(d['charge_cents'])}" for d in p["delivery_charges"][:3])
            text += (f" Delivery: {ch}." if en else f" ডেলিভারি: {ch}।")
        actions = [{"label": "View product" if en else "প্রোডাক্ট দেখুন", "path": f"/shop/{p['slug']}"}]
    else:
        text = "Here are the closest matches:" if en else "সবচেয়ে কাছের প্রোডাক্টগুলো:"
        actions = [{"label": "Browse all" if en else "সব দেখুন", "path": "/shop"}]
    return _out(text, "product", "Live store data" if en else "লাইভ স্টোর ডেটা", lang=lang,
                products=[_card(p) for p in items], actions=actions, followups=_FOLLOW[lang])


def category(cat: dict[str, Any], items: list[dict[str, Any]], lang: str, index: dict[str, Any]) -> dict[str, Any]:
    en = lang == "en"
    text = (f"Yes, we have {cat['name']}. Here are a few:" if en else f"হ্যাঁ, আমাদের {cat['name']} আছে। কয়েকটি দেখুন:") if items else (
        f"We have a {cat['name']} section." if en else f"আমাদের {cat['name']} সেকশন আছে।")
    return _out(text, "category", "Live store data" if en else "লাইভ স্টোর ডেটা", lang=lang,
                products=[_card(p) for p in items],
                actions=[{"label": f"See {cat['name']}" if en else f"{cat['name']} দেখুন", "path": f"/shop?category={cat['slug']}"}],
                followups=_FOLLOW[lang])


def structured(index: dict[str, Any], intents: set[str], lang: str) -> dict[str, Any] | None:
    en = lang == "en"
    if "payment" in intents and index.get("payments"):
        ways = ", ".join(index["payments"])
        text = f"You can pay with: {ways}." if en else f"পেমেন্ট করা যাবে: {ways}।"
        return _out(text, "payment", "Store payment settings" if en else "স্টোরের পেমেন্ট সেটিংস", lang=lang, followups=_FOLLOW[lang])

    if "delivery" in intents:
        d = index.get("delivery") or {}
        charges = d.get("charges") or []
        parts = []
        if charges:
            ch = ", ".join(f"{c['name']} {taka(c['charge_cents'])}" for c in charges)
            parts.append(f"Delivery charge: {ch}." if en else f"ডেলিভারি চার্জ: {ch}।")
        if d.get("free_over_cents"):
            parts.append(f"Free delivery on orders over {taka(d['free_over_cents'])}." if en else f"{taka(d['free_over_cents'])}-এর বেশি অর্ডারে ডেলিভারি ফ্রি।")
        if parts:
            return _out(" ".join(parts), "delivery", "Store delivery settings" if en else "স্টোরের ডেলিভারি সেটিংস", lang=lang, followups=_FOLLOW[lang])

    if "discount" in intents:
        events = index.get("events") or []
        if events:
            now = ", ".join(f"{e['name']} ({e['pct']}% off)" for e in events[:3])
            text = f"Running now: {now}." if en else f"এখন চলছে: {now}।"
        else:
            text = "There are no offers running right now." if en else "এই মুহূর্তে কোনো অফার চলছে না।"
        return _out(text, "discount", "Store offers" if en else "স্টোরের অফার", lang=lang,
                    actions=[{"label": "See offers" if en else "অফার দেখুন", "path": "/shop"}] if events else [], followups=_FOLLOW[lang])

    if intents & {"contact", "location", "track", "order"}:
        ch = contact_channels(index)
        c = index.get("contact") or {}
        lines = []
        if intents & {"location"} and c.get("address"):
            lines.append(f"We're at {c['address']}." if en else f"আমাদের ঠিকানা: {c['address']}।")
        if intents & {"track", "order"}:
            lines.append("For orders and tracking, message us with your order number." if en else "অর্ডার বা ট্র্যাকিংয়ের জন্য অর্ডার নম্বর সহ মেসেজ করুন।")
        if intents & {"contact"} or not lines:
            lines.append("You can reach the team here:" if en else "টিমের সাথে যোগাযোগ করুন:")
        if ch or lines:
            return _out(" ".join(lines), "contact", "Store contact info" if en else "স্টোরের যোগাযোগ", lang=lang, contact=ch)
    return None


def fallback(index: dict[str, Any], lang: str, name: str) -> dict[str, Any]:
    en = lang == "en"
    thin = not index.get("ready")
    who = name or ("this store" if en else "এই স্টোর")
    if thin:
        text = (f"I'm still learning about {who}, so I don't have enough information to answer that yet. For now, please ask the team directly:"
                if en else f"আমি {who} সম্পর্কে এখনো শিখছি, তাই এই প্রশ্নের উত্তর দেওয়ার মতো যথেষ্ট তথ্য আমার কাছে নেই। আপাতত সরাসরি টিমকে জিজ্ঞেস করুন:")
    else:
        text = ("I don't have a good answer for that one yet. Please ask the team and they'll help:"
                if en else "এই প্রশ্নের ভালো উত্তর আমার কাছে এখনো নেই। টিমকে জিজ্ঞেস করুন, তারা সাহায্য করবে:")
    ch = contact_channels(index)
    actions = [] if ch else [{"label": "Contact us" if en else "যোগাযোগ করুন", "path": "/contact"}]
    return _out(text, "fallback", "", lang=lang, fallback="thin" if thin else "unknown",
                contact=ch, actions=actions, followups=_FOLLOW[lang] if not thin else [])
