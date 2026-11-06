"""Checks for the emails and phone numbers people type into public forms.

Why this exists: the demo asks for an email and verifies nothing, so people type
anything. Those addresses went into the marketing lists and came back as bounced
mail, which also hurts delivery for the real ones.

What this can and can't do: it stops throwaway domains, typos and obvious fakes.
It can NOT tell whether a well-formed address like someone@gmail.com really
exists; only sending to it proves that (trial signup does, with its email code).

Used for NEW signups, the demo and the contact form. Deliberately not on login or
password reset: those must keep working for existing accounts, whatever their
address looks like.
"""

import re

# Change this list to accept another provider.
ALLOWED_EMAIL_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "yahoo.com", "hotmail.com", "outlook.com", "live.com",
    "msn.com", "icloud.com", "me.com", "proton.me", "protonmail.com",
})

# Close misspellings get a helpful hint instead of a flat refusal.
_TYPO_HINTS = {
    "gmaio.com": "gmail.com", "gmial.com": "gmail.com", "gmai.com": "gmail.com", "gamil.com": "gmail.com",
    "gmal.com": "gmail.com", "gmail.co": "gmail.com", "gmail.con": "gmail.com", "gmail.cm": "gmail.com",
    "yaho.com": "yahoo.com", "yahoo.co": "yahoo.com", "hotmai.com": "hotmail.com", "hotmial.com": "hotmail.com",
    "outlok.com": "outlook.com",
}

_FAKE_LOCAL = re.compile(
    r"^(test|tester|testing|demo|fake|sample|example|asdf|asdfgh|qwerty|qwe|abc|abcd|abcdef|xyz|xxx|"
    r"aaa|user|admin|noreply|no-reply|nomail|none|null|temp|spam|hello|foo|bar)[._-]?\d*$"
)
_SAME_CHAR = re.compile(r"^(.)\1+$")
GMAIL_MIN_LOCAL = 6  # Gmail doesn't allow shorter usernames


def clean_signup_email(value: str) -> str:
    """Lowercased address, or ValueError with a message safe to show the user."""
    email = str(value).strip().lower()
    local, _, domain = email.rpartition("@")
    if domain in _TYPO_HINTS:
        raise ValueError(f"Did you mean @{_TYPO_HINTS[domain]}? Please check your email.")
    if domain not in ALLOWED_EMAIL_DOMAINS:
        raise ValueError("Please use a Gmail, Yahoo, Outlook, Hotmail or iCloud email address.")
    plain = local.split("+", 1)[0]
    if _FAKE_LOCAL.match(plain) or _SAME_CHAR.match(plain.replace(".", "")):
        raise ValueError("Please enter your real email address.")
    if domain in ("gmail.com", "googlemail.com") and len(plain.replace(".", "")) < GMAIL_MIN_LOCAL:
        raise ValueError("That Gmail address looks too short. Please check it.")
    return email


def is_mailable(email: str) -> bool:
    """False for addresses that would fail clean_signup_email: collected before
    these checks existed, and likely to bounce if mailed."""
    try:
        clean_signup_email(email)
    except ValueError:
        return False
    return True


def is_fake_bd_phone(digits: str) -> bool:
    """`digits` is a valid-format 01XXXXXXXXX. Catches 01711111111, 01712345678."""
    tail = digits[3:]
    return len(set(tail)) == 1 or tail in ("12345678", "87654321", "01234567")
