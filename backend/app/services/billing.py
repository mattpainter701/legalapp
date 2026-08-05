import asyncio
import logging

import stripe
from decimal import Decimal

from app.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

# Cost per 1M tokens (USD)
DEEPSEEK_INPUT_COST_PER_M = Decimal("0.27")
DEEPSEEK_OUTPUT_COST_PER_M = Decimal("1.10")
CLAUDE_INPUT_COST_PER_M = Decimal("3.00")
CLAUDE_OUTPUT_COST_PER_M = Decimal("15.00")
GEMINI_INPUT_COST_PER_M = Decimal("0.075")
GEMINI_OUTPUT_COST_PER_M = Decimal("0.30")
AZURE_INPUT_COST_PER_M = Decimal("3.00")
AZURE_OUTPUT_COST_PER_M = Decimal("6.00")

# PAYG markup multiplier
PAYG_MARKUP = Decimal("10")


def calculate_cost(
    tokens_in: int,
    tokens_out: int,
    model: str,
    billing_tier: str = "payg",
) -> Decimal:
    """
    Calculate cost in USD for a given model and token counts.
    PAYG tier has a 10x markup.
    """
    model_lower = model.lower()

    if ":free" in model_lower or model_lower.endswith("-free"):
        input_cost = Decimal("0")
        output_cost = Decimal("0")
    elif "claude" in model_lower or "anthropic" in model_lower:
        input_cost = CLAUDE_INPUT_COST_PER_M * Decimal(tokens_in) / Decimal(1_000_000)
        output_cost = (
            CLAUDE_OUTPUT_COST_PER_M * Decimal(tokens_out) / Decimal(1_000_000)
        )
    elif "gemini" in model_lower:
        input_cost = GEMINI_INPUT_COST_PER_M * Decimal(tokens_in) / Decimal(1_000_000)
        output_cost = (
            GEMINI_OUTPUT_COST_PER_M * Decimal(tokens_out) / Decimal(1_000_000)
        )
    elif "gpt-4" in model_lower or "azure" in model_lower:
        # Azure OpenAI (GPT-4o, etc.)
        input_cost = AZURE_INPUT_COST_PER_M * Decimal(tokens_in) / Decimal(1_000_000)
        output_cost = AZURE_OUTPUT_COST_PER_M * Decimal(tokens_out) / Decimal(1_000_000)
    elif "deepseek" in model_lower:
        input_cost = DEEPSEEK_INPUT_COST_PER_M * Decimal(tokens_in) / Decimal(1_000_000)
        output_cost = (
            DEEPSEEK_OUTPUT_COST_PER_M * Decimal(tokens_out) / Decimal(1_000_000)
        )
    else:
        # Unknown aliases must not silently inherit an unrelated provider's
        # price. LiteLLM's spend ledger remains the canonical reconciliation
        # source until an explicit price is configured here.
        logger.warning("No application billing rate configured for model %s", model)
        input_cost = Decimal("0")
        output_cost = Decimal("0")

    base_cost = input_cost + output_cost

    if billing_tier == "payg":
        return (base_cost * PAYG_MARKUP).quantize(Decimal("0.000001"))

    return base_cost.quantize(Decimal("0.000001"))


class BillingService:
    def __init__(self):
        stripe.api_key = settings.STRIPE_SECRET_KEY

    async def construct_event(self, payload: bytes, sig_header: str) -> stripe.Event:
        """Verify and construct a Stripe webhook event."""
        return await asyncio.to_thread(
            stripe.Webhook.construct_event,
            payload,
            sig_header,
            settings.STRIPE_WEBHOOK_SECRET,
        )

    async def get_subscription(self, subscription_id: str) -> stripe.Subscription:
        """Retrieve a Stripe subscription."""
        return await asyncio.to_thread(stripe.Subscription.retrieve, subscription_id)

    async def update_customer_metadata(
        self, customer_id: str, metadata: dict
    ) -> stripe.Customer:
        """Update Stripe customer metadata."""
        return await asyncio.to_thread(
            stripe.Customer.modify, customer_id, metadata=metadata
        )

    async def create_customer(
        self, email: str, name: str, tenant_id: str
    ) -> stripe.Customer:
        """Create a new Stripe customer."""
        return await asyncio.to_thread(
            stripe.Customer.create,
            email=email,
            name=name,
            metadata={"tenant_id": tenant_id},
        )

    def get_tier_from_subscription(self, subscription: stripe.Subscription) -> str:
        """Determine billing tier from subscription status."""
        if subscription.status in ("active", "trialing"):
            # Check if there's a flat-rate plan via metadata or plan interval
            items = subscription.get("items", {}).get("data", [])
            for item in items:
                plan = item.get("plan", {})
                if plan.get("interval") == "month":
                    return "flat"
            return "flat"
        return "payg"
