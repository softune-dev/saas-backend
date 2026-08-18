"""Web Push — real OS-level browser notifications, even with the dashboard
tab closed. Only used for new orders (app/api/public.py's
create_public_order); site publish/unpublish only get the in-app bell.

VAPID keys (one-time, per deployment) were generated with:

    python -c "
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization
    import base64
    def b64url(data): return base64.urlsafe_b64encode(data).rstrip(b'=').decode()
    priv = ec.generate_private_key(ec.SECP256R1())
    pub_bytes = priv.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    priv_bytes = priv.private_numbers().private_value.to_bytes(32, 'big')
    print('VAPID_PUBLIC_KEY=' + b64url(pub_bytes))
    print('VAPID_PRIVATE_KEY=' + b64url(priv_bytes))
    "

and stored in .env / dashboard/.env.local (NEXT_PUBLIC_VAPID_PUBLIC_KEY must
match VAPID_PUBLIC_KEY here exactly). Like cache.py/queue.py, every failure
here logs and swallows — a broken push send must never fail the order.
"""

import json
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import PushSubscription

logger = logging.getLogger(__name__)


async def send_order_push(db: AsyncSession, *, site_id: uuid.UUID, title: str, body: str, url: str) -> None:
    if not settings.vapid_public_key or not settings.vapid_private_key:
        return  # not configured — silently skip, same as an unset Cloudinary/Gemini key

    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        logger.warning("pywebpush not installed — run `pip install pywebpush` to enable push notifications")
        return

    subs = (
        await db.execute(select(PushSubscription).where(PushSubscription.site_id == site_id))
    ).scalars().all()
    if not subs:
        return

    payload = json.dumps({"title": title, "body": body, "url": url})
    stale: list[uuid.UUID] = []

    for sub in subs:
        try:
            webpush(
                subscription_info={
                    "endpoint": sub.endpoint,
                    "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
                },
                data=payload,
                vapid_private_key=settings.vapid_private_key,
                vapid_claims={"sub": settings.vapid_subject},
            )
        except WebPushException as exc:
            status_code = getattr(exc.response, "status_code", None)
            if status_code in (404, 410):
                # Browser unsubscribed or the subscription expired — the push
                # service will never accept it again, so stop trying.
                stale.append(sub.id)
            else:
                logger.warning("Push send failed (site_id=%s): %s", site_id, exc)
        except Exception:
            logger.exception("Unexpected error sending push (site_id=%s)", site_id)

    if stale:
        try:
            await db.execute(
                PushSubscription.__table__.delete().where(PushSubscription.id.in_(stale))
            )
            await db.commit()
        except Exception:
            logger.exception("Failed to clean up stale push subscriptions")
            await db.rollback()
