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
