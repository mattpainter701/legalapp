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

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING
import math
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.platform import PlatformSetting


MICROS_PER_USD = 1_000_000

# Bumped whenever a rate below changes. Every reservation records the version it
# was priced under so a mid-window price change stays auditable.
PRICE_CARD_VERSION = "2026-08-27.2"

PRICE_CARD_SETTING_KEY = "ai_price_card_v1"

# USD per million tokens. Provider-qualified keys are deliberate: two providers
# can expose the same model id at different prices, so an unqualified model must
# never inherit a rate merely because its spelling happens to match.
#
# OpenCode Go rates are the published August 27, 2026 rates. Peak rates are used
# for time-variable DeepSeek models because admission must reserve the most the
# request can cost. Cached-write rates are retained where the provider publishes
# them; reservation uses the most expensive possible input category.
_DEFAULT_RATES: dict[str, dict[str, float]] = {
    "opencode-go/grok-4.6": {
        "input": 2.00,
        "output": 6.00,
        "cached_read": 0.50,
        "threshold_tokens": 200_000,
        "input_over_threshold": 4.00,
        "output_over_threshold": 12.00,
        "cached_read_over_threshold": 1.00,
    },
    "opencode-go/gpt-5.6-luna": {
        "input": 0.20,
        "output": 1.20,
        "cached_read": 0.02,
        "cached_write": 0.25,
        "threshold_tokens": 272_000,
        "input_over_threshold": 0.40,
        "output_over_threshold": 1.80,
        "cached_read_over_threshold": 0.04,
        "cached_write_over_threshold": 0.50,
    },
    "opencode-go/glm-5.3-flash": {
        "input": 0.15,
        "output": 0.50,
        "cached_read": 0.03,
    },
    "opencode-go/glm-5.3": {
        "input": 1.40,
        "output": 4.40,
        "cached_read": 0.26,
    },
    "opencode-go/glm-5.2": {
        "input": 1.40,
        "output": 4.40,
        "cached_read": 0.26,
    },
    "opencode-go/glm-5.1": {
        "input": 1.40,
        "output": 4.40,
        "cached_read": 0.26,
    },
    "opencode-go/kimi-k3": {
        "input": 3.00,
        "output": 15.00,
        "cached_read": 0.30,
    },
    "opencode-go/kimi-k2.7-code": {
        "input": 0.95,
        "output": 4.00,
        "cached_read": 0.19,
    },
    "opencode-go/kimi-k2.6": {
        "input": 0.95,
        "output": 4.00,
        "cached_read": 0.16,
    },
    "opencode-go/longcat-2.0": {
        "input": 0.30,
        "output": 1.20,
        "cached_read": 0.006,
    },
    "opencode-go/mimo-v2.5": {
        "input": 0.14,
        "output": 0.28,
        "cached_read": 0.0028,
    },
    "opencode-go/mimo-v2.5-pro": {
        "input": 0.435,
        "output": 0.87,
        "cached_read": 0.003625,
    },
    "opencode-go/minimax-m3": {
        "input": 0.30,
        "output": 1.20,
        "cached_read": 0.06,
    },
    "opencode-go/minimax-m2.7": {
        "input": 0.30,
        "output": 1.20,
        "cached_read": 0.06,
        "cached_write": 0.375,
    },
    "opencode-go/minimax-m2.5": {
        "input": 0.30,
        "output": 1.20,
        "cached_read": 0.06,
    },
    "opencode-go/muse-spark-1.2-contributor": {
        "input": 0.10,
        "output": 0.20,
        "cached_read": 0.002,
    },
    "opencode-go/qwen3.8-max": {
        "input": 2.00,
        "output": 6.00,
        "cached_read": 0.25,
        "cached_write": 2.50,
    },
    "opencode-go/qwen3.7-max": {
        "input": 2.50,
        "output": 7.50,
        "cached_read": 0.50,
        "cached_write": 3.125,
    },
    "opencode-go/qwen3.7-plus": {
        "input": 0.40,
        "output": 1.60,
        "cached_read": 0.04,
        "cached_write": 0.50,
        "threshold_tokens": 256_000,
        "input_over_threshold": 1.20,
        "output_over_threshold": 4.80,
        "cached_read_over_threshold": 0.12,
        "cached_write_over_threshold": 1.50,
    },
    "opencode-go/qwen3.6-plus": {
        "input": 0.50,
        "output": 3.00,
        "cached_read": 0.05,
        "cached_write": 0.625,
        "threshold_tokens": 256_000,
        "input_over_threshold": 2.00,
        "output_over_threshold": 6.00,
        "cached_read_over_threshold": 0.20,
        "cached_write_over_threshold": 2.50,
    },
    "opencode-go/deepseek-v4-pro": {
        "input": 1.32,
        "output": 3.96,
        "cached_read": 0.044,
    },
    "opencode-go/deepseek-v4-flash": {
        "input": 0.44,
        "output": 1.32,
        "cached_read": 0.014,
    },
    "opencode-go/deepseek-v4-flash-vision-exp": {
        "input": 0.44,
        "output": 1.32,
        "cached_read": 0.014,
    },
    "opencode-go/hy3": {
        "input": 0.14,
        "output": 0.58,
        "cached_read": 0.035,
    },
    # Legacy static aliases remain for Standard/Premium accounting callers.
    # Dynamic Background admission never prices one of these aliases.
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
        key = (model or "").strip().lower()
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

    @staticmethod
    def _effective_rate(
        rate: dict[str, float], context_tokens: int
    ) -> dict[str, float]:
        threshold = int(rate.get("threshold_tokens") or 0)
        if threshold <= 0 or max(0, int(context_tokens)) <= threshold:
            return rate
        effective = dict(rate)
        for field in ("input", "output", "cached_read", "cached_write"):
            over = rate.get(f"{field}_over_threshold")
            if over is not None:
                effective[field] = float(over)
        return effective

    def has_rate(self, model: str) -> bool:
        """True when this card can price the model, without raising."""

        try:
            self.rate_for(model)
        except UnknownModelPrice:
            return False
        return True

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
        # Providers define long-context tiers by the request context, not only
        # the prompt. Include the full possible output when deciding which tier
        # to reserve so crossing the threshold during generation cannot make the
        # hold too small.
        rate = self._effective_rate(
            rate, max(0, int(input_tokens)) + max(0, int(max_output_tokens))
        )
        # A cache write can be more expensive than ordinary input. We do not
        # know the provider's cache decision before dispatch, so reserve every
        # input token at the most expensive possible input category.
        worst_input_rate = max(
            Decimal(str(rate["input"])),
            Decimal(str(rate.get("cached_read", 0.0))),
            Decimal(str(rate.get("cached_write", 0.0))),
        )
        micros = Decimal(max(0, int(input_tokens))) * worst_input_rate + Decimal(
            max(0, int(max_output_tokens))
        ) * Decimal(str(rate["output"]))
        # Always round up: a reservation must never under-reserve.
        return max(1, int(micros.to_integral_value(rounding=ROUND_CEILING)))

    def estimate_max_for_models(
        self,
        *,
        models: list[str],
        input_tokens: int,
        max_output_tokens: int,
    ) -> tuple[int, str]:
        """Return the largest reservation across every eligible route target.

        Every target is priced before choosing the maximum. This means one
        unpriced alternate or fallback fails admission closed instead of being
        ignored merely because another target has a known rate.
        """

        if not models:
            raise UnknownModelPrice("")
        estimates = [
            (
                self.estimate_max_micros(
                    model=model,
                    input_tokens=input_tokens,
                    max_output_tokens=max_output_tokens,
                ),
                model,
            )
            for model in dict.fromkeys(models)
        ]
        return max(estimates, key=lambda item: item[0])

    def actual_micros(
        self,
        *,
        model: str,
        tokens_in: int,
        tokens_out: int,
        cached_read_tokens: int = 0,
        cached_write_tokens: int = 0,
    ) -> int:
        rate = self._effective_rate(
            self.rate_for(model), max(0, int(tokens_in)) + max(0, int(tokens_out))
        )
        total_input = max(0, int(tokens_in))
        cached_read = min(total_input, max(0, int(cached_read_tokens)))
        cached_write = min(total_input - cached_read, max(0, int(cached_write_tokens)))
        uncached = total_input - cached_read - cached_write
        micros = (
            Decimal(uncached) * Decimal(str(rate["input"]))
            + Decimal(cached_read)
            * Decimal(str(rate.get("cached_read", rate["input"])))
            + Decimal(cached_write)
            * Decimal(str(rate.get("cached_write", rate["input"])))
            + Decimal(max(0, int(tokens_out))) * Decimal(str(rate["output"]))
        )
        return max(0, int(micros.to_integral_value(rounding=ROUND_CEILING)))


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
        if (
            not math.isfinite(input_rate)
            or not math.isfinite(output_rate)
            or input_rate <= 0
            or output_rate <= 0
        ):
            continue
        parsed: dict[str, float] = {"input": input_rate, "output": output_rate}
        optional_fields = (
            "cached_read",
            "cached_write",
            "threshold_tokens",
            "input_over_threshold",
            "output_over_threshold",
            "cached_read_over_threshold",
            "cached_write_over_threshold",
        )
        valid = True
        for field in optional_fields:
            if field not in value:
                continue
            try:
                optional_value = float(value[field])
            except (TypeError, ValueError):
                valid = False
                break
            if (
                not math.isfinite(optional_value)
                or optional_value < 0
                or (
                    field in {"input_over_threshold", "output_over_threshold"}
                    and optional_value == 0
                )
            ):
                valid = False
                break
            parsed[field] = optional_value
        if valid:
            rates[model.strip().lower()] = parsed
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
