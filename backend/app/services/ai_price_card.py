"""Versioned provider price card for Background Automations admission control.

The Background pool is metered in provider value, not request counts. OpenCode Go
publishes its limits as dollar windows and presents request counts only as an
estimate, so admission has to reason about spend before the request leaves.

Money is tracked in integer USD micros (1 USD == 1_000_000 micros). Floating
point dollars accumulate rounding error across thousands of reservations and a
quota ledger cannot afford it.

An unknown price is never treated as free. ``estimate_max_micros`` raises
``UnknownModelPrice`` so the caller fails admission closed, per the product plan:
"Unknown price or unknown quota consumption fails closed for background
admission; it does not become zero."
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.platform import PlatformSetting


MICROS_PER_USD = 1_000_000

# Bumped whenever a rate below changes. Every reservation records the version it
# was priced under so a mid-window price change stays auditable.
PRICE_CARD_VERSION = "2026-08-27.1"

PRICE_CARD_SETTING_KEY = "ai_price_card_v1"

# USD per million tokens, keyed by the LiteLLM alias the broker actually sends.
# Rates are provider list prices recorded at the version date above; they are
# planning inputs for admission control, not a billing source of truth.
_DEFAULT_RATES: dict[str, dict[str, float]] = {
    "clarity-background": {"input": 0.60, "output": 2.40},
    "clarity-standard": {"input": 0.60, "output": 2.40},
    "clarity-premium": {"input": 3.00, "output": 15.00},
}

# Characters per token. Deliberately low so the character-based estimate rounds
# token count up rather than down — an underestimate here becomes an overspend.
CHARS_PER_TOKEN = 3.5


class UnknownModelPrice(RuntimeError):
    """The active price card has no entry for this model, so cost is unknown."""

    code = "unknown_model_price"

    def __init__(self, model: str) -> None:
        super().__init__(f"No price card entry for model {model!r}")
        self.model = model


@dataclass(frozen=True)
class PriceCard:
    version: str
    rates: dict[str, dict[str, float]]

    def rate_for(self, model: str) -> dict[str, float]:
        key = (model or "").strip()
        if not key:
            raise UnknownModelPrice(model)
        rate = self.rates.get(key)
        if rate is None:
            # Revisioned aliases resolve to their family rate:
            # "clarity-background-r7" prices as "clarity-background".
            base = key.rsplit("-r", 1)[0]
            rate = self.rates.get(base)
        if rate is None:
            raise UnknownModelPrice(model)
        return rate

    def estimate_max_micros(
        self,
        *,
        model: str,
        input_tokens: int,
        max_output_tokens: int,
    ) -> int:
        """Price the worst case this request can cost.

        Output is charged at the full requested budget because that is the
        ceiling the provider may bill. Settlement later replaces this with
        actual usage.
        """

        # A rate is USD per million tokens, so tokens * rate is already micros:
        #   usd     = tokens * rate / 1_000_000
        #   micros  = usd * 1_000_000 = tokens * rate
        rate = self.rate_for(model)
        micros = max(0, int(input_tokens)) * float(rate["input"]) + max(
            0, int(max_output_tokens)
        ) * float(rate["output"])
        # Always round up: a reservation must never under-reserve.
        return max(1, math.ceil(micros))

    def actual_micros(self, *, model: str, tokens_in: int, tokens_out: int) -> int:
        rate = self.rate_for(model)
        micros = max(0, int(tokens_in)) * float(rate["input"]) + max(
            0, int(tokens_out)
        ) * float(rate["output"])
        return max(0, math.ceil(micros))


def estimate_tokens_from_text(text: str) -> int:
    """Bound an input payload's token count without a provider tokenizer.

    Used only to size a reservation. Settlement uses the provider's reported
    usage, so this needs to be conservative rather than exact.
    """

    if not text:
        return 0
    return max(1, -(-len(text) * 100 // int(CHARS_PER_TOKEN * 100)))


def default_price_card() -> PriceCard:
    return PriceCard(version=PRICE_CARD_VERSION, rates=dict(_DEFAULT_RATES))


def _coerce_rates(raw: Any) -> dict[str, dict[str, float]]:
    if not isinstance(raw, dict):
        return {}
    rates: dict[str, dict[str, float]] = {}
    for model, value in raw.items():
        if not isinstance(model, str) or not isinstance(value, dict):
            continue
        try:
            input_rate = float(value["input"])
            output_rate = float(value["output"])
        except (KeyError, TypeError, ValueError):
            continue
        if input_rate < 0 or output_rate < 0:
            continue
        rates[model.strip()] = {"input": input_rate, "output": output_rate}
    return rates


async def get_price_card(db: AsyncSession) -> PriceCard:
    """Load the operator-managed price card, falling back to the built-in rates.

    Operators can correct a provider rate without a deploy. A stored card
    replaces the built-in rates for the models it names and keeps the rest, so a
    partial override cannot silently drop pricing for another model.
    """

    try:
        row = await db.scalar(
            select(PlatformSetting).where(PlatformSetting.key == PRICE_CARD_SETTING_KEY)
        )
    except Exception:
        # Pricing must not be the reason a request fails. The built-in card is
        # the safe fallback; an unknown *model* still fails admission closed.
        return default_price_card()
    raw = getattr(row, "value", None)
    value = raw if isinstance(raw, dict) else {}
    overrides = _coerce_rates(value.get("rates"))
    if not overrides:
        return default_price_card()
    version = value.get("version")
    merged = {**_DEFAULT_RATES, **overrides}
    return PriceCard(
        version=str(version) if version else f"{PRICE_CARD_VERSION}+override",
        rates=merged,
    )


def usd(micros: int) -> float:
    """Present micros as dollars for operator-facing output."""

    return round(int(micros) / MICROS_PER_USD, 4)
