"""Applying a payment claim once it is verified: the plan upgrade or credit
grant, the confirmation email and the dashboard notification.

Two callers reach the same outcome through this one function: the bKash SMS
webhook (app/api/public.py, automatic match on TrxID) and the superadmin
"Approve" button on the Billing page (app/api/superadmin_billing.py, a person
who checked the payment by hand). One code path means a hand-approved claim
leaves exactly the same tenant, invoice and credit state as an automatic one.

The caller must already have flipped the claim to status="verified" and
committed, so a crash part way through leaves a visible "verified but not
applied" state instead of a silent double apply on retry.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import ai_images, billing_actions, chat_credits, invoices as invoices_module, mailer, notifications
from app.models import PaymentClaim, Site, Tenant, User


async def apply_verified_claim(db: AsyncSession, claim: PaymentClaim) -> dict:
    tenant = (await db.execute(select(Tenant).where(Tenant.id == claim.tenant_id))).scalar_one()
    owner = (
        await db.execute(
            select(User).where(User.tenant_id == tenant.id, User.role == "owner")
        )
    ).scalars().first()
    # Dashboard bell/toast (5s poll — see dashboard/lib/api/notifications.ts)
    # is site-scoped, same as Orders/Analytics/etc; a tenant's first site is
    # the same resolution app/api/billing.py's submit_manual_payment already
    # uses for its own email.
    site = (
        await db.execute(select(Site).where(Site.tenant_id == tenant.id).limit(1))
    ).scalars().first()

    if claim.kind == "plan":
        await billing_actions.apply_paid_plan(db, tenant, claim.plan)
        plan_name = invoices_module.PLAN_NAMES.get(claim.plan, claim.plan)
        if owner is not None:
            subject, html_body, text_body = mailer.plan_payment_confirmed_email(
                recipient_name=owner.full_name,
                plan_name=plan_name,
                amount_taka=claim.amount_cents // 100,
                trx_id=claim.trx_id,
            )
            await mailer.send_email(owner.email, subject, html_body, text_body)
        if site is not None:
            await notifications.notify(
                db,
                tenant_id=tenant.id,
                site_id=site.id,
                type="payment_verified",
                title="Payment confirmed",
                body=f"Your {plan_name} plan is now active.",
                link="/settings/billing",
            )
        return {"ok": True, "tenant_id": str(tenant.id), "kind": "plan", "plan": claim.plan}

    # image_credits / chat_credits — same grant_credits(db, tenant_id,
    # credits, reason, reference) signature on both modules (see each
    # module's own docstring), so one branch covers both currencies.
    credit_module = ai_images if claim.kind == "image_credits" else chat_credits
    credit_label = "AI image" if claim.kind == "image_credits" else "AI chat"
    await credit_module.grant_credits(
        db, tenant.id, claim.credits, reason="purchase", reference=claim.trx_id
    )
    if owner is not None:
        subject, html_body, text_body = mailer.credit_purchase_confirmed_email(
            recipient_name=owner.full_name,
            credit_kind_label=credit_label,
            credits=claim.credits,
            amount_taka=claim.amount_cents // 100,
            trx_id=claim.trx_id,
        )
        await mailer.send_email(owner.email, subject, html_body, text_body)
    if site is not None:
        await notifications.notify(
            db,
            tenant_id=tenant.id,
            site_id=site.id,
            type="credit_purchase_verified",
            title="Payment confirmed",
            body=f"{claim.credits} {credit_label} credits added to your account.",
            link="/settings/billing",
        )
    return {
        "ok": True,
        "tenant_id": str(tenant.id),
        "kind": claim.kind,
        "credits": claim.credits,
    }
