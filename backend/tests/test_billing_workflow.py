"""Unit tests for billing workflow helpers (timer rounding, invoice
numbering, status transitions) — pure logic, no DB required."""

from datetime import date
from decimal import Decimal

from app.services.billing_workflow import (
    can_transition_invoice,
    is_invoice_overdue,
    next_invoice_number,
    round_timer_hours,
)


class TestRoundTimerHours:
    def test_rounds_up_to_six_minute_increment(self):
        # 20 minutes → 0.4h (four 6-minute increments, rounded up from 3.33)
        assert round_timer_hours(20 * 60, 6) == Decimal("0.40")

    def test_exact_increment_not_bumped(self):
        # Exactly 12 minutes → 0.2h, no extra increment
        assert round_timer_hours(12 * 60, 6) == Decimal("0.20")

    def test_minimum_one_increment(self):
        # 30 seconds still bills one 6-minute increment
        assert round_timer_hours(30, 6) == Decimal("0.10")

    def test_zero_elapsed_bills_minimum(self):
        assert round_timer_hours(0, 6) == Decimal("0.10")

    def test_custom_fifteen_minute_increment(self):
        # 16 minutes at quarter-hour rounding → 0.5h
        assert round_timer_hours(16 * 60, 15) == Decimal("0.50")

    def test_invalid_increment_falls_back_to_default(self):
        assert round_timer_hours(60, 0) == Decimal("0.10")
        assert round_timer_hours(60, None) == Decimal("0.10")

    def test_one_hour_exact(self):
        assert round_timer_hours(3600, 6) == Decimal("1.00")


class TestNextInvoiceNumber:
    def test_first_invoice_of_year(self):
        assert next_invoice_number([], 2026) == "INV-2026-0001"

    def test_increments_highest_sequence(self):
        existing = ["INV-2026-0001", "INV-2026-0007", "INV-2026-0003"]
        assert next_invoice_number(existing, 2026) == "INV-2026-0008"

    def test_ignores_other_years(self):
        existing = ["INV-2025-0099", "INV-2026-0002"]
        assert next_invoice_number(existing, 2026) == "INV-2026-0003"

    def test_ignores_legacy_random_suffixes(self):
        # Pre-overhaul numbers used random hex suffixes — must not break sequencing
        existing = ["INV-2026-A1B2C3", "INV-2026-0004"]
        assert next_invoice_number(existing, 2026) == "INV-2026-0005"

    def test_grows_past_four_digits(self):
        assert next_invoice_number(["INV-2026-9999"], 2026) == "INV-2026-10000"


class TestInvoiceTransitions:
    def test_draft_to_sent_allowed(self):
        assert can_transition_invoice("draft", "sent")

    def test_draft_to_paid_blocked(self):
        assert not can_transition_invoice("draft", "paid")

    def test_sent_to_paid_allowed(self):
        assert can_transition_invoice("sent", "paid")

    def test_paid_is_terminal(self):
        assert not can_transition_invoice("paid", "sent")
        assert not can_transition_invoice("paid", "void")

    def test_void_is_terminal(self):
        assert not can_transition_invoice("void", "sent")

    def test_same_status_is_noop_allowed(self):
        assert can_transition_invoice("sent", "sent")

    def test_partially_paid_to_paid(self):
        assert can_transition_invoice("partially_paid", "paid")

    def test_sent_to_written_off(self):
        assert can_transition_invoice("sent", "written_off")


class TestIsInvoiceOverdue:
    def test_sent_past_due_is_overdue(self):
        assert is_invoice_overdue("sent", date(2026, 1, 1), today=date(2026, 2, 1))

    def test_sent_on_due_date_not_overdue(self):
        assert not is_invoice_overdue("sent", date(2026, 2, 1), today=date(2026, 2, 1))

    def test_paid_never_overdue(self):
        assert not is_invoice_overdue("paid", date(2026, 1, 1), today=date(2026, 2, 1))

    def test_draft_never_overdue(self):
        assert not is_invoice_overdue("draft", date(2026, 1, 1), today=date(2026, 2, 1))

    def test_partially_paid_past_due_is_overdue(self):
        assert is_invoice_overdue(
            "partially_paid", date(2026, 1, 1), today=date(2026, 2, 1)
        )
