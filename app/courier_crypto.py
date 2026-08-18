"""Encrypt/decrypt courier API credentials at rest.

A merchant's Steadfast/Pathao/RedX secret key is exactly as sensitive as a
payment credential — anyone who reads it out of the database could book (and
get paid for) fake shipments, or cancel real ones, on the merchant's own
account. It must never sit in Postgres as plaintext, so every write goes
through `encrypt()` first and every read goes through `decrypt()`.

Fernet (symmetric, authenticated encryption) rather than a database-level
option like pgcrypto: keeping the key in application config, not the
database, means a leaked DB backup alone can't decrypt the credentials it
contains — you'd also need COURIER_CREDENTIALS_KEY from the app's own .env.
"""

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException, status

from app.config import settings


@lru_cache
def _fernet() -> Fernet:
    if not settings.courier_credentials_key:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Courier integrations aren't configured. Set COURIER_CREDENTIALS_KEY "
            "in .env (generate one with: python -c \"from cryptography.fernet "
            "import Fernet; print(Fernet.generate_key().decode())\").",
        )
    try:
        return Fernet(settings.courier_credentials_key.encode())
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "COURIER_CREDENTIALS_KEY is not a valid Fernet key.",
        ) from exc


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as exc:
        # Only reachable if COURIER_CREDENTIALS_KEY changed after credentials
        # were saved, or the column was tampered with — either way, the
        # stored value is unusable, not a bug in the caller.
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Stored courier credentials could not be decrypted. The "
            "connection needs to be re-created.",
        ) from exc


def mask(api_key: str, visible: int = 4) -> str:
    """Display-safe hint — the only fragment of a credential ever serialized
    back to the client. Matches dashboard/lib/api/courier.ts's expectation."""
    if not api_key:
        return "••••"
    return f"••••••{api_key[-visible:]}"
