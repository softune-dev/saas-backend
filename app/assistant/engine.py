"""Answering one question from a store's index.

Order of preference, most trustworthy first:
  1. small talk (hello / thanks), so a greeting never hits "I don't know"
  2. the merchant's own written answers and FAQs, in the merchant's words
  3. a product (price / stock / options read LIVE, never from the index)
  4. facts the store already gave us (payments, delivery, contact, offers)
  5. a category ("do you have panjabi?")
  6. an honest fallback: say so, and hand over to a person

Why this order: a wrong answer costs the merchant a sale and the customer's
trust, an honest "I'm not sure, ask the team" costs nothing. So every step has
a confidence bar, and anything below it falls through to the next one instead
of guessing.
"""

from collections.abc import Awaitable, Callable
from typing import Any

from app.assistant import render
from app.assistant.text import GENERIC, concepts_in, content_tokens, lang_of, tokens

LiveLoader = Callable[[list[str]], Awaitable[dict[str, dict[str, Any]]]]

# Concepts that carry the question's meaning; the rest only colour it.
PRIMARY = {"price", "stock", "delivery", "return", "payment", "discount", "contact", "location",
           "size", "color", "warranty", "track", "order", "about"}
HELPER = {"howmuch", "day", "hello", "thanks"}

_FAQ_ACCEPT = 0.55  # idf-weighted share of the question's words found in an entry
_CONCEPT_ACCEPT = 0.6  # score given to a pure-concept match (see _best_entry)
_PRODUCT_ACCEPT = 0.5


def _weight(index: dict[str, Any], t: str) -> float:
    """A word that appears in none of the store's answers is the most specific
    thing in the question ("cars" in "do you sell cars"), so it weighs the
    most. Giving it a low default is what let an unrelated About answer win."""
    return index["idf"].get(t, index.get("idf_unseen", 3.0))


def _best_entry(index: dict[str, Any], qt: list[str]) -> tuple[dict[str, Any] | None, float]:
    """The written answer that best matches. Two ways to match:
    - words: how much of the question's weight appears in the entry's question
    - concept: a generic policy question ("delivery koto din lagbe") shares
      no exact words with "How long does delivery take?", but both are about
      delivery. Only used when the question names no specific thing."""
    qset = [t for t in dict.fromkeys(qt) if t not in HELPER]
    if not qset:
        return None, 0.0
    total = sum(_weight(index, t) for t in qset)
    q_primary = concepts_in(qt) & PRIMARY
    specific = [t for t in content_tokens(qt)]

    best: dict[str, Any] | None = None
    best_score = 0.0
    for e in index["entries"]:
        et = set(e["t"])
        score = sum(_weight(index, t) for t in qset if t in et) / total
        # Question side only: an answer that merely MENTIONS "WhatsApp" is not
        # an answer to "what is your number".
        if q_primary and not specific and e["kind"] != "about" and q_primary <= concepts_in(e["t"]):
            score = max(score, _CONCEPT_ACCEPT)
        # The merchant's own answers break ties over generic FAQ text.
        if e["kind"] == "custom":
            score += 0.02
        if score > best_score:
            best, best_score = e, score
    return best, best_score


def _match_products(index: dict[str, Any], qt: list[str]) -> list[dict[str, Any]]:
    want = [t for t in dict.fromkeys(content_tokens(qt))]
    if not want or not index["products"]:
        return []
    # Rarer words identify a product better ("nova" beats "pro").
    n = len(index["products"])
    df: dict[str, int] = {}
    for p in index["products"]:
        for t in set(p["nt"]) | set(p["ct"]):
            df[t] = df.get(t, 0) + 1
    w = {t: (n + 1) / (df.get(t, 0) + 0.5) for t in want}
    total = sum(w.values())
    scored = []
    for p in index["products"]:
        name = set(p["nt"])
        hit_name = [t for t in want if t in name]
        if not hit_name:
            continue
        hit_cat = [t for t in want if t in set(p["ct"]) and t not in name]
        score = (sum(w[t] for t in hit_name) + 0.4 * sum(w[t] for t in hit_cat)) / total
        # Bonus when every word of the product's name was typed.
        if name and name <= set(want):
            score += 0.2
        scored.append((score, p))
    scored.sort(key=lambda x: -x[0])
    if not scored or scored[0][0] < _PRODUCT_ACCEPT:
        return []
    top = scored[0][0]
    return [p for s, p in scored[:3] if s >= top * 0.75]


def _match_category(index: dict[str, Any], qt: list[str]) -> dict[str, Any] | None:
    want = set(content_tokens(qt))
    best, best_n = None, 0
    for c in index["categories"]:
        ct = set(c["t"])
        hit = len(ct & want)
        if hit and hit == len(ct) and hit > best_n:
            best, best_n = c, hit
    return best


async def respond(index: dict[str, Any], question: str, live: LiveLoader, *, store_name: str = "") -> dict[str, Any]:
    lang = lang_of(question)
    qt = tokens(question)
    intents = concepts_in(qt)
    name = store_name or index.get("name") or ""

    # 1. Small talk, only when nothing else was asked.
    if intents & {"hello", "thanks"} and not (intents & PRIMARY) and not content_tokens(qt):
        return render.smalltalk("thanks" if "thanks" in intents else "hello", lang, name, index)

    # "Tell me about your shop": the About text, before "shop" is read as a
    # question about where the shop is.
    if "about" in intents:
        about = next((e for e in index["entries"] if e["kind"] == "about"), None)
        if about is not None:
            return render.entry(about, lang, index)

    # 2. Written answers (merchant Q&A, FAQs).
    entry, score = _best_entry(index, qt)
    if entry is not None and score >= _FAQ_ACCEPT:
        return render.entry(entry, lang, index)

    # 3. A specific product.
    matched = _match_products(index, qt)
    if matched:
        rows = await live([p["id"] for p in matched])
        products = [rows[p["id"]] for p in matched if p["id"] in rows]
        if products:
            return render.products(products, intents, lang, index)

    # 4. Facts the store already told us.
    structured = render.structured(index, intents, lang)
    if structured is not None:
        return structured

    # 5. A category.
    cat = _match_category(index, qt)
    if cat is not None:
        in_cat = [p for p in index["products"] if p["cat"] == cat["name"]][:3]
        rows = await live([p["id"] for p in in_cat]) if in_cat else {}
        products = [rows[p["id"]] for p in in_cat if p["id"] in rows]
        return render.category(cat, products, lang, index)

    # 6. Honest fallback.
    return render.fallback(index, lang, name)


__all__ = ["respond", "GENERIC"]
