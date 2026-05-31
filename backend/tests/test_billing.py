"""
Tests for the billing cost calculation logic.
Stripe webhook handling is not tested here (requires live Stripe key).
"""

from decimal import Decimal

import pytest

from app.services.billing import (
    CLAUDE_INPUT_COST_PER_M,
    CLAUDE_OUTPUT_COST_PER_M,
    DEEPSEEK_INPUT_COST_PER_M,
    DEEPSEEK_OUTPUT_COST_PER_M,
    PAYG_MARKUP,
    calculate_cost,
)


class TestCalculateCost:
    # ------------------------------------------------------------------
    # DeepSeek pricing
    # ------------------------------------------------------------------

    def test_deepseek_flat_tier_input_only(self):
        cost = calculate_cost(1_000_000, 0, "deepseek-chat", billing_tier="flat")
        assert cost == DEEPSEEK_INPUT_COST_PER_M.quantize(Decimal("0.000001"))

    def test_deepseek_flat_tier_output_only(self):
        cost = calculate_cost(0, 1_000_000, "deepseek-chat", billing_tier="flat")
        assert cost == DEEPSEEK_OUTPUT_COST_PER_M.quantize(Decimal("0.000001"))

    def test_deepseek_payg_markup(self):
        flat = calculate_cost(100_000, 200_000, "deepseek-chat", billing_tier="flat")
        payg = calculate_cost(100_000, 200_000, "deepseek-chat", billing_tier="payg")
        assert payg == (flat * PAYG_MARKUP).quantize(Decimal("0.000001"))

    def test_deepseek_zero_tokens(self):
        cost = calculate_cost(0, 0, "deepseek-chat", billing_tier="payg")
        assert cost == Decimal("0.000000")

    def test_deepseek_v4_flash_alias_uses_deepseek_rates(self):
        cost_v4 = calculate_cost(500_000, 500_000, "deepseek-v4-flash", billing_tier="flat")
        cost_chat = calculate_cost(500_000, 500_000, "deepseek-chat", billing_tier="flat")
        assert cost_v4 == cost_chat

    # ------------------------------------------------------------------
    # Claude pricing
    # ------------------------------------------------------------------

    def test_claude_flat_tier_input_only(self):
        cost = calculate_cost(1_000_000, 0, "claude-opus-4-8", billing_tier="flat")
        assert cost == CLAUDE_INPUT_COST_PER_M.quantize(Decimal("0.000001"))

    def test_claude_flat_tier_output_only(self):
        cost = calculate_cost(0, 1_000_000, "claude-opus-4-8", billing_tier="flat")
        assert cost == CLAUDE_OUTPUT_COST_PER_M.quantize(Decimal("0.000001"))

    def test_claude_payg_markup(self):
        flat = calculate_cost(100_000, 50_000, "claude-opus-4-8", billing_tier="flat")
        payg = calculate_cost(100_000, 50_000, "claude-opus-4-8", billing_tier="payg")
        assert payg == (flat * PAYG_MARKUP).quantize(Decimal("0.000001"))

    def test_claude_more_expensive_than_deepseek(self):
        ds = calculate_cost(100_000, 100_000, "deepseek-chat", billing_tier="flat")
        cl = calculate_cost(100_000, 100_000, "claude-opus-4-8", billing_tier="flat")
        assert cl > ds

    def test_anthropic_keyword_routes_to_claude(self):
        cost_anthropic = calculate_cost(100_000, 100_000, "anthropic-model", billing_tier="flat")
        cost_claude = calculate_cost(100_000, 100_000, "claude-opus-4-8", billing_tier="flat")
        assert cost_anthropic == cost_claude

    # ------------------------------------------------------------------
    # Precision
    # ------------------------------------------------------------------

    def test_result_has_six_decimal_places(self):
        cost = calculate_cost(12345, 67890, "deepseek-chat", billing_tier="flat")
        assert cost == cost.quantize(Decimal("0.000001"))

    def test_payg_result_has_six_decimal_places(self):
        cost = calculate_cost(12345, 67890, "claude-opus-4-8", billing_tier="payg")
        assert cost == cost.quantize(Decimal("0.000001"))

    # ------------------------------------------------------------------
    # Typical usage
    # ------------------------------------------------------------------

    def test_typical_chat_message_cost_is_small(self):
        # ~500 tokens in, ~300 out — should be sub-cent even for Claude PAYG
        cost = calculate_cost(500, 300, "claude-opus-4-8", billing_tier="payg")
        assert cost < Decimal("0.10")

    def test_large_document_analysis_cost_reasonable(self):
        # ~50k tokens in, ~2k out on DeepSeek flat
        cost = calculate_cost(50_000, 2_000, "deepseek-chat", billing_tier="flat")
        assert Decimal("0.00001") < cost < Decimal("1.00")
