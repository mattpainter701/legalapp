import asyncio
import logging
from datetime import datetime, timedelta, timezone

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.services.access_control import require_finance_admin
from app.models.mcp_product import MCPUsageEvent
from app.models.tenant import Tenant
from app.services.stripe_webhook_guard import claim_event, ordering_object_id

settings = get_settings()
router = APIRouter(prefix="/billing", tags=["billing"])
logger = logging.getLogger(__name__)


def _mask(val: str | None) -> str | None:
    """Truncate a Stripe identifier for display or logging.

    Stripe customer and subscription ids identify a paying firm, so they are
    treated as sensitive wherever they leave the database -- in API responses
    and equally in log output. Enough of the id survives to correlate a record
    with the Stripe dashboard without writing it out in full.
    """
    if not val:
        return None
    return val[:8] + "..." + val[-4:]


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

    since = datetime.now(timezone.utc) - timedelta(days=30)
    mcp_usage = (
        await db.execute(
            select(
                func.count(MCPUsageEvent.id).label("calls"),
                func.coalesce(func.sum(MCPUsageEvent.result_count), 0).label("results"),
            ).where(
                MCPUsageEvent.tenant_id == tenant.id,
                MCPUsageEvent.product_key_id.is_not(None),
                MCPUsageEvent.created_at >= since,
            )
        )
    ).one()

    return {
        "billing_tier": tenant.billing_tier,
        "stripe_customer_id": _mask(tenant.stripe_customer_id),
        "stripe_subscription_id": _mask(tenant.stripe_subscription_id),
        # Surfaced so the billing page can notice a tier that disagrees with the
        # subscription Stripe actually holds, rather than presenting a stale
        # downgrade as a plan and upselling the firm on what it already bought.
        "subscription_status": tenant.stripe_subscription_status,
        "billing_status": tenant.mcp_billing_status,
        "flat_seat_count": tenant.flat_seat_count,
        "mcp_usage": {
            "mode": "payg",
            "line_item": "MCP usage",
            "meter": "mcp_product_key_calls",
            "calls_30d": int(mcp_usage.calls or 0),
            "results_30d": int(mcp_usage.results or 0),
        },
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

    handler = _SUBSCRIPTION_HANDLERS.get(event_type)
    if handler is None:
        logger.debug("Unhandled Stripe event type: %s", event_type)
        return {"status": "ok", "event_type": event_type}

    # Claim before dispatching. This rejects a retried delivery and, more
    # importantly, refuses an event that is older than one already applied to
    # the same subscription -- Stripe does not guarantee order, and applying a
    # stale cancellation after a resubscription downgrades a paying firm.
    claim = await claim_event(
        db,
        event_id=str(event["id"]),
        event_type=event_type,
        event_created=int(event.get("created") or 0),
        object_id=ordering_object_id(event_type, event_data),
    )
    if not claim.should_process:
        # Nothing for Stripe to retry: the effect is already applied, or
        # deliberately not applied because newer state won.
        return {"status": "skipped", "reason": claim.reason, "event_type": event_type}

    # Deliberately not caught. A handler that fails must return 5xx so Stripe
    # retries; swallowing it would drop the event permanently. The claim row is
    # rolled back with the failed transaction, so the retry can claim it again.
    await handler(db, event_data)
    await db.commit()

    return {"status": "ok", "event_type": event_type}


async def _find_tenant_by_customer(
    db: AsyncSession, customer_id: str, *, event_type: str
) -> Tenant | None:
    """Resolve the tenant a Stripe customer belongs to, loudly.

    A webhook for a customer we cannot place is not routine: it usually means a
    checkout completed without ``stripe_customer_id`` ever being persisted, so
    the firm is paying and no part of the application knows. Log at error level
    rather than returning ``None`` silently.
    """
    result = await db.execute(
        select(Tenant).where(Tenant.stripe_customer_id == customer_id)
    )
    tenant = result.scalar_one_or_none()
    if tenant is None:
        logger.error(
            "Stripe %s references customer %s with no matching tenant. A paying "
            "customer may be unlinked -- reconcile stripe_customer_id.",
            event_type,
            _mask(customer_id),
        )
    return tenant


async def _handle_subscription_updated(db: AsyncSession, subscription: dict) -> None:
    customer_id = subscription.get("customer")
    if not customer_id:
        return

    tenant = await _find_tenant_by_customer(
        db, customer_id, event_type="customer.subscription.updated"
    )
    if tenant is None:
        return

    status = subscription.get("status", "")
    subscription_id = subscription.get("id")
    tenant.stripe_subscription_id = subscription_id
    tenant.stripe_subscription_status = status or "unknown"

    if status in ("active", "trialing"):
        items = subscription.get("items", {}).get("data", [])
        billing_tier = None
        for item in items:
            plan = item.get("plan", {})
            metadata = plan.get("metadata", {})
            if metadata.get("tier"):
                billing_tier = metadata["tier"]
                break
        if billing_tier is None:
            # Falling through to a default here silently places every
            # subscriber on a misconfigured Stripe price onto that tier. Keep
            # the tenant's existing tier and make the misconfiguration visible.
            billing_tier = tenant.billing_tier or "flat"
            logger.error(
                "Stripe subscription %s for customer %s has no plan metadata "
                "'tier'. Keeping existing tier %r -- set metadata.tier on the "
                "Stripe price.",
                _mask(subscription_id),
                _mask(customer_id),
                billing_tier,
            )
        tenant.billing_tier = billing_tier
        tenant.is_active = True
        tenant.mcp_billing_status = "active"
    else:
        tenant.billing_tier = "payg"
        tenant.mcp_billing_status = (
            "past_due" if status in ("unpaid", "past_due") else "suspended"
        )
        if status == "canceled":
            tenant.stripe_subscription_id = None


async def _handle_subscription_deleted(db: AsyncSession, subscription: dict) -> None:
    customer_id = subscription.get("customer")
    if not customer_id:
        return

    tenant = await _find_tenant_by_customer(
        db, customer_id, event_type="customer.subscription.deleted"
    )
    if tenant is None:
        return

    tenant.billing_tier = "payg"
    tenant.stripe_subscription_id = None
    tenant.stripe_subscription_status = "canceled"
    tenant.mcp_billing_status = "suspended"


async def _handle_payment_failed(db: AsyncSession, invoice: dict) -> None:
    customer_id = invoice.get("customer")
    if not customer_id:
        return

    tenant = await _find_tenant_by_customer(
        db, customer_id, event_type="invoice.payment_failed"
    )
    if tenant is None:
        return

    tenant.stripe_subscription_status = "past_due"
    tenant.mcp_billing_status = "past_due"


async def _handle_payment_succeeded(db: AsyncSession, invoice: dict) -> None:
    customer_id = invoice.get("customer")
    if not customer_id:
        return
    tenant = await _find_tenant_by_customer(db, customer_id, event_type="invoice.paid")
    if tenant is None:
        return
    if tenant.stripe_subscription_id:
        tenant.stripe_subscription_status = "active"
        tenant.mcp_billing_status = "active"


# Dispatch table shared with the /api/billing/webhooks/stripe alias so both
# routes interpret subscription lifecycle events identically.
_SUBSCRIPTION_HANDLERS = {
    "customer.subscription.updated": _handle_subscription_updated,
    "customer.subscription.deleted": _handle_subscription_deleted,
    "invoice.payment_failed": _handle_payment_failed,
    "invoice.paid": _handle_payment_succeeded,
    "invoice.payment_succeeded": _handle_payment_succeeded,
}
