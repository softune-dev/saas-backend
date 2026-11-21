"""The storefront assistant: matching, fallbacks, live prices, and the API.

The first half needs no database: it builds an index from plain data and asks
it questions, the same way scripts/assistant_eval.py does against real stores.
The second half goes through the real public API like a storefront visitor.
"""

import pytest

from app.assistant.engine import respond
from app.assistant.index import build_index
from app.assistant.schemas import AssistantSettings, settings_from_row
from app.assistant.text import lang_of, tokens

PRODUCTS = [
    {"id": "p1", "slug": "ivory-panjabi", "name": "Ivory Embroidered Panjabi", "category_name": "Panjabi",
     "delivery_charges": [{"name": "Inside Dhaka", "charge_cents": 6000}, {"name": "Outside Dhaka", "charge_cents": 12000}]},
    {"id": "p2", "slug": "gold-koti", "name": "Gold Brocade Koti", "category_name": "Koti"},
    {"id": "p3", "slug": "linen-koti", "name": "Charcoal Linen Koti", "category_name": "Koti"},
    {"id": "p4", "slug": "chadar", "name": "Cotton Chadar", "category_name": "Shawls"},
    {"id": "p5", "slug": "nagra", "name": "Embroidered Nagra", "category_name": "Footwear"},
]
FAQS = [
    {"question": "How long does delivery take?", "answer": "Inside Dhaka in 24 hours, outside Dhaka in 2 to 3 days."},
    {"question": "Can I return or exchange?", "answer": "Yes, within 7 days if unused."},
    {"question": "Can I pay cash on delivery?", "answer": "Yes, cash on delivery works everywhere."},
]


def data(**over):
    base = {
        "name": "Raaj", "faqs": FAQS, "products": PRODUCTS,
        "categories": [{"id": "c1", "slug": "koti", "name": "Koti"}],
        "payment_methods": [{"provider": "cod", "label": "Cash on Delivery"}],
        "business": {"phone": "01700000000", "whatsapp": "01700000000", "email": "hi@raaj.test",
                     "address": {"street": "House 1", "city": "Dhaka"}},
        "shipping": {"free_over_cents": 300000},
        "events": [{"name": "Eid Sale", "discount_percent": 20}],
    }
    base.update(over)
    return base


def make_live(prices: dict[str, int]):
    async def live(ids):
        return {
            i: {"id": i, "slug": i, "name": i, "image": "", "price_cents": prices[i], "compare_at_cents": None,
                "in_stock": True, "variants": [], "delivery_charges": []}
            for i in ids if i in prices
        }
    return live


async def ask(index, q, prices=None):
    return await respond(index, q, make_live(prices or {"p1": 349000, "p2": 269000, "p3": 219000, "p4": 149000, "p5": 219000}), store_name="Raaj")


# ---------------------------------------------------------------------------
#  Text: the Bangla tokenizer regression that made every Bangla question fail
# ---------------------------------------------------------------------------


def test_bangla_words_are_not_chopped_at_vowel_signs():
    # \w does not match Bengali vowel signs, so a naive tokenizer split
    # "ডেলিভারি" into fragments and no Bangla question ever matched.
    assert tokens("ডেলিভারি চার্জ কত") == ["delivery", "price", "howmuch"]


def test_same_concept_in_english_banglish_and_bangla():
    assert "delivery" in tokens("delivery") and "delivery" in tokens("deliver korben") and "delivery" in tokens("ডেলিভারিতে")


def test_language_follows_the_script_typed():
    assert lang_of("দাম কত") == "bn"
    assert lang_of("dam koto") == "en"


# ---------------------------------------------------------------------------
#  Answers
# ---------------------------------------------------------------------------


async def test_product_price_is_read_live_not_from_the_index():
    index = build_index(data())
    first = await ask(index, "how much is Ivory Embroidered Panjabi")
    assert "৳3,490" in first["answer"]
    # Same index object, price changed in the database: the answer follows at
    # once, with no rebuild. That is why prices are never stored in the index.
    second = await ask(index, "how much is Ivory Embroidered Panjabi", {"p1": 299000})
    assert "৳2,990" in second["answer"]
    assert second["products"][0]["price_cents"] == 299000


async def test_merchant_written_answer_beats_the_faq():
    own = {"q": "How long does delivery take?", "a": "Same day in Gulshan, 2 days elsewhere."}
    index = build_index(data(qa=[own]))
    r = await ask(index, "how long does delivery take")
    assert r["kind"] == "custom" and "Gulshan" in r["answer"]


async def test_delivery_charge_question_gets_the_charges_not_the_delivery_time():
    r = await ask(build_index(data()), "delivery charge koto")
    assert r["kind"] == "delivery" and "৳60" in r["answer"] and "৳3,000" in r["answer"]


async def test_bangla_question_gets_a_bangla_answer():
    r = await ask(build_index(data()), "পেমেন্ট কিভাবে করব")
    assert r["lang"] == "bn" and r["answered"]


async def test_unrelated_question_is_not_forced_into_a_match():
    for q in ("do you sell cars", "who is the president of france"):
        r = await ask(build_index(data()), q)
        assert r["kind"] == "fallback", q


async def test_a_word_in_the_about_text_does_not_hijack_an_unrelated_question():
    about = {"paragraphs": ["We sell panjabi and answer on WhatsApp every day."]}
    r = await ask(build_index(data(about=about)), "do you sell cars")
    assert r["kind"] == "fallback"


# ---------------------------------------------------------------------------
#  A new store with little data
# ---------------------------------------------------------------------------


async def test_new_store_says_it_is_still_learning_and_points_to_the_team():
    index = build_index({"name": "Fresh Shop", "business": {"whatsapp": "01711111111"}})
    assert index["ready"] is False
    r = await ask(index, "do you ship to Sylhet")
    assert r["kind"] == "fallback" and r["fallback"] == "thin"
    assert "still learning" in r["answer"]
    assert r["contact"]["whatsapp"].startswith("https://wa.me/8801711111111")


async def test_established_store_uses_the_plain_fallback():
    index = build_index(data())
    assert index["ready"] is True
    r = await ask(index, "do you sell cars")
    assert r["fallback"] == "unknown" and "still learning" not in r["answer"]


async def test_small_talk_never_falls_back():
    index = build_index({"name": "Fresh Shop"})
    assert (await ask(index, "hello"))["kind"] == "smalltalk"
    assert (await ask(index, "thanks"))["kind"] == "smalltalk"


async def test_store_with_nothing_still_answers_gracefully():
    r = await ask(build_index({}), "delivery charge koto")
    assert r["kind"] == "fallback" and r["answered"] is False


# ---------------------------------------------------------------------------
#  Settings
# ---------------------------------------------------------------------------


def test_default_settings_are_on():
    assert AssistantSettings().enabled is True
    assert settings_from_row(None).enabled is True
    assert settings_from_row({}).launcher_text == "Let's Talk"


def test_bad_stored_settings_fall_back_to_defaults_instead_of_breaking_the_chat():
    assert settings_from_row({"accent": "red", "position": "up"}).position == "right"


def test_accent_must_be_a_hex_colour():
    with pytest.raises(ValueError):
        AssistantSettings(accent="red")
    assert AssistantSettings(accent="#e8431a").accent == "#E8431A"


def test_qa_list_is_capped():
    with pytest.raises(ValueError):
        AssistantSettings(qa=[{"q": f"q{i}?", "a": "a"} for i in range(101)])


# ---------------------------------------------------------------------------
#  The public API, as a storefront visitor
# ---------------------------------------------------------------------------


async def _published_host(account, site) -> str:
    published = await account.post(f"/sites/{site['id']}/publish", json={})
    assert published.status_code == 200
    return published.json()["subdomain"]


async def test_visitor_asks_and_gets_the_live_price(account, site):
    product = (
        await account.post(f"/sites/{site['id']}/products", json={"name": "Zorblax Gadget", "price_cents": 129000, "stock": 5})
    ).json()
    host = await _published_host(account, site)

    cfg = (await account.client.get(f"/public/site/{host}/assistant")).json()
    assert cfg["enabled"] is True and cfg["name"] == "Assistant"

    reply = (await account.client.post(f"/public/site/{host}/assistant/ask", json={"question": "how much is Zorblax Gadget"})).json()
    assert reply["kind"] == "product" and "৳1,290" in reply["answer"]

    # Price edit in the dashboard is visible to the very next question.
    await account.patch(f"/sites/{site['id']}/products/{product['id']}", json={"price_cents": 99000})
    reply = (await account.client.post(f"/public/site/{host}/assistant/ask", json={"question": "how much is Zorblax Gadget"})).json()
    assert "৳990" in reply["answer"]


async def test_unpublished_site_has_no_assistant(account, site):
    host = site["subdomain"]
    assert (await account.client.get(f"/public/site/{host}/assistant")).status_code == 404
    assert (await account.client.post(f"/public/site/{host}/assistant/ask", json={"question": "hi"})).status_code == 404


async def test_empty_or_oversized_question_is_rejected(account, site):
    host = await _published_host(account, site)
    assert (await account.client.post(f"/public/site/{host}/assistant/ask", json={"question": ""})).status_code == 422
    assert (await account.client.post(f"/public/site/{host}/assistant/ask", json={"question": "x" * 301})).status_code == 422


# ---------------------------------------------------------------------------
#  The storefront script (one file for every theme)
# ---------------------------------------------------------------------------


async def test_widget_script_is_public_cacheable_javascript(client):
    res = await client.get("/public/assistant/widget.js")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("application/javascript")
    # Cacheable and shared: it holds only code, never store data.
    assert "public" in res.headers["cache-control"]
    assert "softuneAssistant" in res.text


def test_widget_never_writes_server_text_as_markup():
    """Everything from the API (answers, names, greetings) reaches the page via
    textContent. innerHTML is allowed exactly once, for the static SVG helper;
    a second use is how a merchant's or shopper's text would start running as
    HTML on a store."""
    from pathlib import Path

    js = (Path(__file__).resolve().parent.parent / "app" / "assistant" / "widget.js").read_text(encoding="utf-8")
    assert js.count(".innerHTML") == 1
    assert "eval(" not in js and "document.write" not in js
