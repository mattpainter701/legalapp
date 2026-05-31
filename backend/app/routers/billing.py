import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models.tenant import Tenant

settings = get_settings()
router = APIRouter(prefix="/billing", tags=["billing"])


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
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
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


async def _find_tenant_by_customer(
    db: AsyncSession, customer_id: str
) -> Tenant | None:
    """Look up a tenant by Stripe customer ID."""
    result = await db.execute(
        select(Tenant).where(Tenant.stripe_customer_id == customer_id)
    )
    return result.scalar_one_or_none()


async def _handle_subscription_updated(db: AsyncSession, subscription: dict) -> None:
    """Update tenant billing tier based on subscription status."""
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
        # Determine tier from subscription items
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
    """Handle subscription cancellation."""
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
    """Handle failed payment — optionally suspend tenant."""
    customer_id = invoice.get("customer")
    if not customer_id:
        return

    tenant = await _find_tenant_by_customer(db, customer_id)
    if tenant is None:
        return

    attempt_count = invoice.get("attempt_count", 1)

    # After 3 failed attempts, downgrade to PAYG
    if attempt_count >= 3:
        tenant.billing_tier = "payg"
        await db.commit()
