"""Stock and sale prices of products with variants, as shoppers see them.

Pure tests (no database): they build products in memory and call the same
functions the public API uses. The Elara case is `test_variant_stock_makes_the_product_buyable`:
77 of 86 products read "out of stock" to Google and Meta because the product's own
`stock` column was 0 while the stock lived in its variants.
"""

import copy
import uuid

from app.api.public import _public_product
from app.models import Event, Product
from app.products import discounted_attributes, stock_status


def product(*, stock=0, track=True, combos=None, price=349000, compare=None) -> Product:
    attrs = {"combinations": combos} if combos is not None else {}
    return Product(
        id=uuid.uuid4(), slug="p", name="Panjabi", description="", short_description="",
        price_cents=price, compare_at_cents=compare, currency="BDT", images=[], video_url=None,
        category_id=None, track_stock=track, stock=stock, attributes=attrs, features=[],
        free_delivery=False, delivery_charges=[],
    )


def combo(stock=5, *, track=True, active=True, price=None, compare=None, key="Size:M"):
    c = {"key": key, "optionValues": {"Size": key[-1]}, "stock": stock, "trackStock": track, "isActive": active}
    if price is not None:
        c["priceCents"] = price
    if compare is not None:
        c["compareAtCents"] = compare
    return c


# ---------------------------------------------------------------------------
#  Stock
# ---------------------------------------------------------------------------


def test_product_without_variants_keeps_the_original_rule():
    assert stock_status(product(stock=0)) == (False, 0)
    assert stock_status(product(stock=4)) == (True, 4)
    assert stock_status(product(stock=0, track=False)) == (True, 0)


def test_variant_stock_makes_the_product_buyable():
    # The Elara bug: product-level stock 0, stock in the variants.
    p = product(stock=0, combos=[combo(8, key="Size:M"), combo(3, key="Size:L")])
    assert stock_status(p) == (True, 11)
    assert _public_product(p, None)["inStock"] is True
    assert _public_product(p, None)["stockCount"] == 11


def test_product_is_out_of_stock_only_when_every_variant_is():
    p = product(stock=99, combos=[combo(0, key="Size:M"), combo(0, key="Size:L")])
    # The product-level number is ignored when variants exist: checkout does the same.
    assert stock_status(p) == (False, 0)
    assert _public_product(p, None)["inStock"] is False


def test_switched_off_variants_do_not_count():
    p = product(combos=[combo(10, active=False, key="Size:M"), combo(2, key="Size:L")])
    assert stock_status(p) == (True, 2)
    assert stock_status(product(combos=[combo(10, active=False)])) == (False, 0)


def test_an_untracked_variant_is_always_buyable():
    p = product(combos=[combo(0, track=False, key="Size:M"), combo(0, key="Size:L")])
    assert stock_status(p)[0] is True


# ---------------------------------------------------------------------------
#  Sale prices on variants
# ---------------------------------------------------------------------------


def test_sale_price_shows_on_variant_prices_as_checkout_charges_it():
    combos = [combo(5, price=349000, compare=399000, key="Size:M"), combo(5, key="Size:L")]
    p = product(price=349000, compare=399000, combos=combos)
    event = Event(discount_percent=20)

    shown = _public_product(p, None, event)
    first, second = shown["attributes"]["combinations"]
    # 20% off 3,490 = 2,792, and the old price becomes the "was" price.
    assert first["priceCents"] == 279200 and first["compareAtCents"] == 349000
    # A variant with no price of its own is left for the storefront to price from the product.
    assert "priceCents" not in second
    assert shown["price"] == 2792.0 and shown["compareAtPrice"] == 3490.0


def test_no_sale_leaves_variant_prices_untouched():
    p = product(combos=[combo(5, price=349000, compare=399000)])
    first = _public_product(p, None)["attributes"]["combinations"][0]
    assert first["priceCents"] == 349000 and first["compareAtCents"] == 399000


def test_sale_adjustment_does_not_edit_the_stored_product():
    attrs = {"combinations": [combo(5, price=100000)], "variants": [{"type": "Size"}]}
    before = copy.deepcopy(attrs)
    out = discounted_attributes(attrs, 10)
    assert attrs == before, "the original attributes must not change"
    assert out["combinations"][0]["priceCents"] == 90000
    assert out["variants"] == [{"type": "Size"}], "everything else is carried over"


def test_sale_price_rounds_in_whole_cents_like_checkout():
    # 12.5% off 99.99 must round half-up in integer cents, never via float.
    out = discounted_attributes({"combinations": [combo(1, price=9999)]}, 13)
    assert out["combinations"][0]["priceCents"] == 8699


# ---------------------------------------------------------------------------
#  Text for search snippets
# ---------------------------------------------------------------------------


def test_missing_short_description_becomes_a_clean_excerpt_of_the_long_one():
    p = product()
    p.description = "<p>Soft cotton &nbsp;panjabi</p><ul><li>Hand embroidered</li></ul>"
    shown = _public_product(p, None)
    assert shown["shortDescription"] == "Soft cotton panjabi Hand embroidered"


def test_long_description_excerpt_is_cut_at_a_word_with_an_ellipsis():
    p = product()
    p.description = "<p>" + "word " * 100 + "</p>"
    excerpt = _public_product(p, None)["shortDescription"]
    assert len(excerpt) <= 161 and excerpt.endswith("…") and "<" not in excerpt


def test_the_merchants_own_short_description_is_never_replaced():
    p = product()
    p.description, p.short_description = "<p>Long</p>", "My own blurb"
    assert _public_product(p, None)["shortDescription"] == "My own blurb"
