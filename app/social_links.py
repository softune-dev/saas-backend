"""Turns what a merchant types for a social page into a safe public link.

These links land in Site.business.socials, which the storefront renders as
<a href> for every shopper, so only https links on the platform's own domain
get through. Anything else (a page *name* with spaces, another website, a
javascript: URL) returns None: sign-up never fails over it, and the raw text
is still kept privately for follow-up.
"""

import re
from urllib.parse import urlsplit

_PLATFORMS = {
    "facebook": (("facebook.com", "fb.com", "fb.me"), "https://www.facebook.com/{}"),
    "instagram": (("instagram.com",), "https://www.instagram.com/{}"),
    "tiktok": (("tiktok.com",), "https://www.tiktok.com/@{}"),
    "youtube": (("youtube.com", "youtu.be"), "https://www.youtube.com/@{}"),
}
_USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{2,100}$")
_BARE_DOMAIN_RE = re.compile(r"^(www\.|m\.|web\.)?(facebook|fb|instagram|tiktok|youtube|youtu)\.(com|me|be)/", re.I)


def public_url(platform: str, raw: str | None) -> str | None:
    value = (raw or "").strip()
    if not value or any(c.isspace() for c in value):
        return None
    hosts, template = _PLATFORMS[platform]

    if _BARE_DOMAIN_RE.match(value):
        value = f"https://{value}"
    if "://" in value:
        parts = urlsplit(value)
        host = (parts.hostname or "").lower()
        if parts.scheme not in ("http", "https") or "@" in parts.netloc:
            return None
        if not any(host == h or host.endswith(f".{h}") for h in hosts):
            return None
        # A bare "facebook.com" points at no page in particular.
        if parts.path in ("", "/"):
            return None
        return parts._replace(scheme="https").geturl()

    if value.lower().removeprefix("www.") in hosts:
        return None
    username = value.lstrip("@")
    return template.format(username) if _USERNAME_RE.match(username) else None


def public_socials(typed: dict[str, str | None]) -> dict[str, str]:
    links = {p: public_url(p, typed.get(p)) for p in _PLATFORMS}
    return {p: url for p, url in links.items() if url}
