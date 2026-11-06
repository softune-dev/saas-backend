"""Custom domain validation. These are the inputs that used to be saved
straight onto a site (a live site once got the text "mehedi store" as its
domain), so the rejects matter as much as the accepts."""

import pytest
from pydantic import ValidationError

from app.config import settings
from app.domains import normalize_domain
from app.schemas import SiteUpdate


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Shop.Example.com", "shop.example.com"),
        ("https://www.Foo.com/path?x=1", "www.foo.com"),
        ("foo.com:8080", "foo.com"),
        ("foo.com.", "foo.com"),
        ("", None),
        ("   ", None),
        (None, None),
    ],
)
def test_normalizes_what_a_merchant_typed(raw, expected):
    assert normalize_domain(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["mehedi store", "rihan-online-shop", "localhost", "1.2.3.4", "a_b.com", "bad..com", "-a.com", "x.vercel.app"],
)
def test_rejects_things_that_are_not_a_domain(raw):
    with pytest.raises(ValueError):
        normalize_domain(raw)


def test_rejects_our_own_base_domain():
    with pytest.raises(ValueError):
        normalize_domain(f"shop.{settings.site_base_domain}")


def test_site_update_runs_the_validator():
    assert SiteUpdate(custom_domain="  Shop.Example.COM ").custom_domain == "shop.example.com"
    assert SiteUpdate(custom_domain="").custom_domain is None
    with pytest.raises(ValidationError):
        SiteUpdate(custom_domain="mehedi store")


def test_edge_config_key_only_uses_allowed_characters():
    """Must match blockedByEdgeConfig in templates/*/middleware.ts, or blocks
    silently stop applying at page level."""
    from app import edge_config

    assert edge_config.key_for("Shop.Example-Store.com") == "ipb_shop_example_store_com"


def test_signup_email_checks():
    import pytest

    from app import contact_checks as c

    assert c.clean_signup_email(" Real.Merchant@Gmail.com ") == "real.merchant@gmail.com"
    assert c.clean_signup_email("shop.owner@outlook.com") == "shop.owner@outlook.com"
    for bad in ("someone@mailinator.com", "owner@ytrr.com", "test@gmail.com", "demo123@gmail.com",
                "aaaaaaa@gmail.com", "abc@gmail.com"):
        with pytest.raises(ValueError):
            c.clean_signup_email(bad)
    with pytest.raises(ValueError, match="gmail.com"):
        c.clean_signup_email("real.merchant@gmaio.com")
    assert not c.is_mailable("x@comzx.com") and c.is_mailable("real.merchant@yahoo.com")


def test_fake_phone_numbers_are_refused_and_phone_is_required_at_signup():
    import pytest
    from pydantic import ValidationError

    from app.schemas import TrialDetailsIn

    ok = TrialDetailsIn(signup_token="t", shop_name="Shop", phone="+880 1712-345679")
    assert ok.phone == "01712345679"
    for bad in ("", "01711111111", "01712345678", "12345"):
        with pytest.raises(ValidationError):
            TrialDetailsIn(signup_token="t", shop_name="Shop", phone=bad)
    with pytest.raises(ValidationError):
        TrialDetailsIn(signup_token="t", shop_name="Shop")


def test_demo_tokens_are_only_reachable_through_the_code_step():
    from app.main import app

    paths = {route.path for route in app.routes}
    assert "/public/demo-access/start" in paths and "/public/demo-access/verify" in paths
    assert "/public/demo-access" not in paths  # the old route handed out tokens with no code
