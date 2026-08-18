"""Router aggregation — one import for main.py."""

from fastapi import APIRouter

from app.api import (
    ai,
    analytics,
    auth,
    commerce,
    courier,
    fraud,
    media,
    notifications,
    pages,
    payments,
    public,
    push,
    sites,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(analytics.router)
api_router.include_router(sites.router)
api_router.include_router(media.router)
api_router.include_router(pages.router)
api_router.include_router(commerce.router)
api_router.include_router(courier.router)
api_router.include_router(payments.router)
api_router.include_router(fraud.router)
api_router.include_router(notifications.router)
api_router.include_router(push.router)
api_router.include_router(public.router)
api_router.include_router(ai.router)
api_router.include_router(ai.chat_router)
api_router.include_router(ai.actions_router)
api_router.include_router(ai.usage_router)
