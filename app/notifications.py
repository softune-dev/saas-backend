"""Dashboard bell notifications — a side effect of other writes.

notify() is called after an order is created or a site is published/
unpublished; it's never load-bearing for that write. Like cache.py and
queue.py, failures here log and swallow — a broken notification insert must
never fail the checkout or the publish it's describing.

Runs its own commit, deliberately separate from the caller's transaction:
the thing being notified about (the order, the publish) has already
succeeded and been committed by the time notify() runs, so there's nothing
to roll back together even if this insert fails.
"""

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Notification

logger = logging.getLogger(__name__)

# Mirrors the CHECK constraint in migrations/019_notifications.sql /
# 021_order_blocked_notification.sql — kept in sync by hand, same as every
# other provider/type enum in this codebase.
_KNOWN_TYPES = {"order_created", "order_blocked", "site_published", "site_unpublished"}


async def notify(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    site_id: uuid.UUID,
    type: str,
    title: str,
    body: str = "",
    link: str | None = None,
) -> None:
    if type not in _KNOWN_TYPES:
        logger.warning("notify(): unknown notification type %r", type)
        return
    try:
        db.add(
            Notification(
                tenant_id=tenant_id,
                site_id=site_id,
                type=type,
                title=title,
                body=body,
                link=link,
            )
        )
        await db.commit()
    except Exception:
        logger.exception(
            "Failed to create notification (type=%s, site_id=%s)", type, site_id
        )
        await db.rollback()
