"""Superadmin email campaigns: pick an audience, write the message, send a test,
then send to everyone. EMAIL ONLY: there is no WhatsApp or SMS path anywhere in
this feature (those accounts keep getting banned).

Sending never happens inside the request. Each recipient becomes one
JOB_SEND_EMAIL on the queue (the same job ticket replies and OTPs use), so a
slow SMTP server can't hold the page and the worker handles delivery. The row
in email_campaigns records what was sent and to how many people.

Cross-tenant by nature (an audience spans tenants), so, like the rest of
/superadmin, it is gated by SuperAdminUser and rule 1 does not apply.
"""

import html
import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import mailer, queue
from app.db import get_db
from app.models import DemoAccessRequest, EmailCampaign, Tenant, User
from app.schemas import ORMModel, Page
from app.security import SuperAdminUser

router = APIRouter(prefix="/superadmin/marketing", tags=["superadmin-marketing"])
DB = Annotated[AsyncSession, Depends(get_db)]

# One send is capped so a mistaken "everyone" can't blast past the SMTP
# provider's daily limit and get the sending address blocked.
MAX_RECIPIENTS = 1000

PLANS = ("trial", "starter", "growth", "business")
STATUSES = ("active", "suspended", "cancelled", "payment_overdue")


class AudienceIn(BaseModel):
    kind: Literal["owners", "demo_leads", "custom"] = "owners"
    # Only for kind="owners". Empty means "any".
    plans: list[str] = Field(default_factory=list)
    statuses: list[str] = Field(default_factory=list)
    # Only for kind="custom".
    emails: list[str] = Field(default_factory=list, max_length=MAX_RECIPIENTS)

    @field_validator("plans")
    @classmethod
    def _plans(cls, v: list[str]) -> list[str]:
        bad = [p for p in v if p not in PLANS]
        if bad:
            raise ValueError(f"Unknown plan: {bad[0]}")
        return v

    @field_validator("statuses")
    @classmethod
    def _statuses(cls, v: list[str]) -> list[str]:
        bad = [s for s in v if s not in STATUSES]
        if bad:
            raise ValueError(f"Unknown status: {bad[0]}")
        return v


class MessageIn(BaseModel):
    subject: str = Field(min_length=1, max_length=200)
    headline: str = Field(default="", max_length=200)
    body: str = Field(min_length=1, max_length=8000)
    cta_label: str | None = Field(default=None, max_length=60)
    cta_url: str | None = Field(default=None, max_length=500)

    @field_validator("cta_url")
    @classmethod
    def _url(cls, v: str | None) -> str | None:
        v = (v or "").strip()
        if not v:
            return None
        if not v.startswith(("https://", "http://")):
            raise ValueError("The button link must start with https://")
        return v


class SendIn(MessageIn):
    audience: AudienceIn


class TestIn(MessageIn):
    to_email: str = Field(min_length=3, max_length=254)


class PreviewOut(BaseModel):
    count: int
    sample: list[str]
    capped: bool


class AudienceCountsOut(BaseModel):
    owners: int
    trial_owners: int
    paid_owners: int
    overdue_owners: int
    demo_leads: int
    max_recipients: int


class CampaignOut(ORMModel):
    id: uuid.UUID
    subject: str
    headline: str
    body: str
    cta_label: str | None
    cta_url: str | None
    audience: dict
    recipient_count: int
    status: str
    sent_at: datetime | None
    created_at: datetime
    created_by_email: str | None = None


def _clean_email(value: str) -> str | None:
    value = value.strip().lower()
    if "@" not in value or " " in value or len(value) > 254:
        return None
    return value


async def _resolve(db: AsyncSession, audience: AudienceIn) -> list[str]:
    """Distinct, lowercase recipient emails for an audience, sorted."""
    emails: set[str] = set()
    if audience.kind == "custom":
        emails.update(filter(None, (_clean_email(e) for e in audience.emails)))
    elif audience.kind == "demo_leads":
        rows = (await db.execute(select(DemoAccessRequest.email))).scalars().all()
        emails.update(e.lower() for e in rows)
    else:
        query = (
            select(User.email)
            .join(Tenant, Tenant.id == User.tenant_id)
            .where(User.role == "owner", User.is_active.is_(True))
        )
        if audience.plans:
            query = query.where(Tenant.plan.in_(audience.plans))
        if audience.statuses:
            query = query.where(Tenant.status.in_(audience.statuses))
        emails.update(e.lower() for e in (await db.execute(query)).scalars().all())
    return sorted(emails)


def _render(message: MessageIn) -> tuple[str, str]:
    """(html_body, text_body) for the composed campaign, using the shared
    Softune email shell. Plain text in, escaped HTML out: blank lines split
    paragraphs, so nothing the operator types can inject markup."""
    paragraphs = [p.strip() for p in message.body.split("\n\n") if p.strip()]
    return mailer.campaign_email(
        headline=message.headline,
        paragraphs=paragraphs,
        cta_label=message.cta_label,
        cta_url=message.cta_url,
        escape=html.escape,
    )


@router.get("/audiences", response_model=AudienceCountsOut)
async def audience_counts(admin: SuperAdminUser, db: DB) -> dict:
    async def owners(*where) -> int:
        return (
            await db.execute(
                select(func.count(func.distinct(User.email)))
                .join(Tenant, Tenant.id == User.tenant_id)
                .where(User.role == "owner", User.is_active.is_(True), *where)
            )
        ).scalar_one()

    demo = (await db.execute(select(func.count(DemoAccessRequest.id)))).scalar_one()
    return {
        "owners": await owners(),
        "trial_owners": await owners(Tenant.plan == "trial"),
        "paid_owners": await owners(Tenant.plan.in_(("starter", "growth", "business"))),
        "overdue_owners": await owners(Tenant.status == "payment_overdue"),
        "demo_leads": demo,
        "max_recipients": MAX_RECIPIENTS,
    }


@router.post("/preview", response_model=PreviewOut)
async def preview_audience(payload: AudienceIn, admin: SuperAdminUser, db: DB) -> dict:
    emails = await _resolve(db, payload)
    return {
        "count": min(len(emails), MAX_RECIPIENTS),
        "sample": emails[:5],
        "capped": len(emails) > MAX_RECIPIENTS,
    }


@router.post("/test", status_code=status.HTTP_204_NO_CONTENT)
async def send_test(payload: TestIn, admin: SuperAdminUser) -> None:
    """Sends the composed message to one address right now, so the operator
    sees exactly what recipients will get before sending to everyone."""
    to_email = _clean_email(payload.to_email)
    if to_email is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Enter a valid email address")
    html_body, text_body = _render(payload)
    ok = await mailer.send_email(to_email, f"[Test] {payload.subject}", html_body, text_body)
    if not ok:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "The test email could not be sent")


@router.post("/campaigns", response_model=CampaignOut, status_code=status.HTTP_201_CREATED)
async def send_campaign(payload: SendIn, admin: SuperAdminUser, db: DB) -> dict:
    emails = await _resolve(db, payload.audience)
    if not emails:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "That audience has no recipients")
    if len(emails) > MAX_RECIPIENTS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"That audience has {len(emails)} recipients; the limit per send is {MAX_RECIPIENTS}. "
            "Narrow it by plan or status.",
        )

    html_body, text_body = _render(payload)
    campaign = EmailCampaign(
        created_by=admin.id,
        subject=payload.subject,
        headline=payload.headline,
        body=payload.body,
        cta_label=payload.cta_label,
        cta_url=payload.cta_url,
        audience=payload.audience.model_dump(exclude={"emails"})
        | ({"email_count": len(payload.audience.emails)} if payload.audience.kind == "custom" else {}),
        recipient_count=len(emails),
        status="sent",
        sent_at=datetime.now(UTC),
    )
    db.add(campaign)
    await db.commit()
    await db.refresh(campaign)

    for email in emails:
        await queue.publish(
            queue.JOB_SEND_EMAIL,
            {"to": email, "subject": payload.subject, "html_body": html_body, "text_body": text_body},
        )
    return _out(campaign, admin.email)


def _out(c: EmailCampaign, created_by_email: str | None) -> dict:
    return {
        "id": c.id,
        "subject": c.subject,
        "headline": c.headline,
        "body": c.body,
        "cta_label": c.cta_label,
        "cta_url": c.cta_url,
        "audience": c.audience,
        "recipient_count": c.recipient_count,
        "status": c.status,
        "sent_at": c.sent_at,
        "created_at": c.created_at,
        "created_by_email": created_by_email,
    }


@router.get("/campaigns", response_model=Page[CampaignOut])
async def list_campaigns(
    admin: SuperAdminUser,
    db: DB,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    total = (await db.execute(select(func.count(EmailCampaign.id)))).scalar_one()
    rows = (
        await db.execute(
            select(EmailCampaign, User.email)
            .outerjoin(User, User.id == EmailCampaign.created_by)
            .order_by(EmailCampaign.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return {
        "items": [_out(c, email) for c, email in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }
