import asyncio
import logging

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.services.access_control import require_finance_admin
from app.models.tenant import Tenant

settings = get_settings()
router = APIRouter(prefix="/billing", tags=["billing"])
logger = logging.getLogger(__name__)


# ── Stripe customer helper (called from auth on tenant creation) ───────────────


async def ensure_stripe_customer(tenant: Tenant, db: AsyncSession) -> None:
    """Create a Stripe customer for the tenant if one doesn't exist yet."""
    if tenant.stripe_customer_id or not settings.STRIPE_SECRET_KEY:
        return
    stripe.api_key = settings.STRIPE_SECRET_KEY
    try:
        customer = await asyncio.to_thread(
            stripe.Customer.create,
            name=tenant.company_name or tenant.name,
            metadata={"tenant_id": str(tenant.id), "domain": tenant.domain},
        )
        tenant.stripe_customer_id = customer["id"]
        await db.flush()
    except stripe.StripeError as exc:
        logger.warning(f"Stripe customer creation failed for tenant {tenant.id}: {exc}")


# ── Self-service billing endpoints ────────────────────────────────────────────


@router.get("/status")
async def billing_status(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Return current billing tier and masked Stripe IDs for the tenant."""
    user = await require_finance_admin(request, db)
    result = await db.execute(select(Tenant).where(Tenant.id == user.tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    def _mask(val: str | None) -> str | None:
        if not val:
            return None
        return val[:8] + "..." + val[-4:]

    return {
        "billing_tier": tenant.billing_tier,
        "stripe_customer_id": _mask(tenant.stripe_customer_id),
        "stripe_subscription_id": _mask(tenant.stripe_subscription_id),
        "flat_seat_count": tenant.flat_seat_count,
    }


@router.post("/checkout-session")
async def create_checkout_session(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Create a Stripe Checkout Session for upgrading to the flat subscription.
    Returns {checkout_url} for the frontend to redirect to.
    """
    if not settings.STRIPE_SECRET_KEY:
        raise HTTPException(status_code=501, detail="Stripe not configured")
    if not settings.STRIPE_PRICE_ID:
        raise HTTPException(status_code=501, detail="STRIPE_PRICE_ID not configured")

    user = await require_finance_admin(request, db)
    result = await db.execute(select(Tenant).where(Tenant.id == user.tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    stripe.api_key = settings.STRIPE_SECRET_KEY
    await ensure_stripe_customer(tenant, db)
    await db.commit()
    await db.refresh(tenant)

    success_url = (
        settings.STRIPE_SUCCESS_URL or f"{settings.FRONTEND_URL}/billing?success=1"
    )
    cancel_url = (
        settings.STRIPE_CANCEL_URL or f"{settings.FRONTEND_URL}/billing?cancel=1"
    )

    try:
        session = await asyncio.to_thread(
            stripe.checkout.Session.create,
            customer=tenant.stripe_customer_id,
            mode="subscription",
            line_items=[
                {
                    "price": settings.STRIPE_PRICE_ID,
                    "quantity": max(tenant.flat_seat_count, 1),
                }
            ],
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={"tenant_id": str(tenant.id)},
        )
    except stripe.StripeError as exc:
        raise HTTPException(status_code=502, detail=f"Stripe error: {exc}")

    return {"checkout_url": session.url}


@router.post("/portal")
async def create_portal_session(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Create a Stripe Customer Portal session so the tenant can manage their subscription.
    Returns {portal_url}.
    """
    if not settings.STRIPE_SECRET_KEY:
        raise HTTPException(status_code=501, detail="Stripe not configured")

    user = await require_finance_admin(request, db)
    result = await db.execute(select(Tenant).where(Tenant.id == user.tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    stripe.api_key = settings.STRIPE_SECRET_KEY
    await ensure_stripe_customer(tenant, db)
    await db.commit()
    await db.refresh(tenant)

    if not tenant.stripe_customer_id:
        raise HTTPException(
            status_code=400, detail="No Stripe customer found for this tenant"
        )

    return_url = f"{settings.FRONTEND_URL}/billing"

    try:
        portal = await asyncio.to_thread(
            stripe.billing_portal.Session.create,
            customer=tenant.stripe_customer_id,
            return_url=return_url,
        )
    except stripe.StripeError as exc:
        raise HTTPException(status_code=502, detail=f"Stripe error: {exc}")

    return {"portal_url": portal.url}


# ── Stripe webhook ─────────────────────────────────────────────────────────────


@router.post("/webhook", status_code=200)
async def stripe_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Handle Stripe webhook events.
    Verifies webhook signature and processes subscription/payment events.
    """
    if not settings.STRIPE_SECRET_KEY or not settings.STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=501, detail="Stripe not configured")

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    if not sig_header:
        raise HTTPException(status_code=400, detail="Missing Stripe signature header")

    stripe.api_key = settings.STRIPE_SECRET_KEY

    try:
        event = await asyncio.to_thread(
            stripe.Webhook.construct_event,
            payload,
            sig_header,
            settings.STRIPE_WEBHOOK_SECRET,
        )
    except stripe.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid Stripe webhook signature")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Webhook error: {str(exc)}")

    event_type = event["type"]
    event_data = event["data"]["object"]

    if event_type == "customer.subscription.updated":
        await _handle_subscription_updated(db, event_data)

    elif event_type == "customer.subscription.deleted":
        await _handle_subscription_deleted(db, event_data)

    elif event_type == "invoice.payment_failed":
        await _handle_payment_failed(db, event_data)

    return {"status": "ok", "event_type": event_type}


async def _find_tenant_by_customer(db: AsyncSession, customer_id: str) -> Tenant | None:
    result = await db.execute(
        select(Tenant).where(Tenant.stripe_customer_id == customer_id)
    )
    return result.scalar_one_or_none()


async def _handle_subscription_updated(db: AsyncSession, subscription: dict) -> None:
    customer_id = subscription.get("customer")
    if not customer_id:
        return

    tenant = await _find_tenant_by_customer(db, customer_id)
    if tenant is None:
        return

    status = subscription.get("status", "")
    subscription_id = subscription.get("id")
    tenant.stripe_subscription_id = subscription_id

    if status in ("active", "trialing"):
        items = subscription.get("items", {}).get("data", [])
        billing_tier = "flat"
        for item in items:
            plan = item.get("plan", {})
            metadata = plan.get("metadata", {})
            if metadata.get("tier"):
                billing_tier = metadata["tier"]
                break
        tenant.billing_tier = billing_tier
        tenant.is_active = True
    elif status in ("canceled", "unpaid", "past_due"):
        tenant.billing_tier = "payg"
        if status == "canceled":
            tenant.stripe_subscription_id = None

    await db.commit()


async def _handle_subscription_deleted(db: AsyncSession, subscription: dict) -> None:
    customer_id = subscription.get("customer")
    if not customer_id:
        return

    tenant = await _find_tenant_by_customer(db, customer_id)
    if tenant is None:
        return

    tenant.billing_tier = "payg"
    tenant.stripe_subscription_id = None
    await db.commit()


async def _handle_payment_failed(db: AsyncSession, invoice: dict) -> None:
    customer_id = invoice.get("customer")
    if not customer_id:
        return

    tenant = await _find_tenant_by_customer(db, customer_id)
    if tenant is None:
        return

    attempt_count = invoice.get("attempt_count", 1)

    if attempt_count >= 3:
        tenant.billing_tier = "payg"
        await db.commit()
