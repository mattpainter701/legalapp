"""Focused coverage for the billing schema validators and pure router helpers."""

from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.routers.billing_extended import (
    _billing_preview_data,
    _deactivate_invoice_payment_link,
    _expense_invoice_amount,
    _parse_uuid,
    _select_requested_sources,
)
from app.schemas.billing import ExpenseCreate, ExpenseResponse, ExpenseUpdate


def _expense_response(**overrides):
    values = {
        "id": uuid4(),
        "tenant_id": uuid4(),
        "matter_id": uuid4(),
        "user_id": uuid4(),
        "description": "Court filing",
        "amount": Decimal("100.00"),
        "date": date(2026, 8, 25),
        "category": "court filing",
        "is_billable": True,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    values.update(overrides)
    return values


def test_expense_create_currency_validator_normalizes_and_rejects_non_alpha():
    payload = ExpenseCreate(
        matter_id=str(uuid4()),
        description="Filing fee",
        amount=Decimal("12.50"),
        date=date(2026, 8, 25),
        currency="usd",
    )
    assert payload.currency == "USD"
    with pytest.raises(ValidationError):
        ExpenseCreate(
            matter_id=str(uuid4()),
            description="Filing fee",
            amount=Decimal("12.50"),
            date=date(2026, 8, 25),
            currency="U$D",
        )


def test_expense_update_optional_currency_and_review_status_validators():
    assert ExpenseUpdate().currency is None
    assert ExpenseUpdate(currency="gbp", review_status="approved").currency == "GBP"
    with pytest.raises(ValidationError):
        ExpenseUpdate(currency="12x")
    with pytest.raises(ValidationError):
        ExpenseUpdate(review_status="unknown")


def test_expense_response_currency_validator_and_uuid_coercion():
    response = ExpenseResponse.model_validate(_expense_response(currency="eur"))
    assert response.currency == "EUR"
    assert isinstance(response.id, str)
    with pytest.raises(ValidationError):
        ExpenseResponse.model_validate(_expense_response(currency="EURO"))


def test_parse_uuid_and_selected_source_error_branches():
    value = uuid4()
    assert _parse_uuid(str(value), "matter") == value
    with pytest.raises(HTTPException, match="Invalid matter ID format"):
        _parse_uuid("not-a-uuid", "matter")

    item = SimpleNamespace(id=value)
    assert _select_requested_sources([item], None, "expense") == [item]
    assert _select_requested_sources([item], [], "expense") == []
    with pytest.raises(HTTPException, match="Invalid selected expense ID"):
        _select_requested_sources([item], ["bad"], "expense")
    with pytest.raises(HTTPException, match="Duplicate selected expense ID"):
        _select_requested_sources([item], [str(value), str(value)], "expense")
    with pytest.raises(HTTPException, match="no longer available"):
        _select_requested_sources([item], [str(uuid4())], "expense")


def test_expense_invoice_amount_prefers_client_amount():
    assert _expense_invoice_amount(
        SimpleNamespace(amount=Decimal("10"), client_amount=None)
    ) == Decimal("10")
    assert _expense_invoice_amount(
        SimpleNamespace(amount=Decimal("10"), client_amount=Decimal("12.5"))
    ) == Decimal("12.5")


@pytest.mark.asyncio
async def test_billing_preview_applies_date_filters(monkeypatch):
    matter = SimpleNamespace(id=uuid4())
    monkeypatch.setattr(
        "app.routers.billing_extended._get_matter_or_404",
        lambda *args: _immediate(matter),
    )

    class Result:
        def scalars(self):
            return self

        def all(self):
            return []

    class DB:
        async def execute(self, statement):
            return Result()

    await _billing_preview_data(
        DB(),
        str(matter.id),
        uuid4(),
        date(2026, 8, 1),
        date(2026, 8, 31),
    )


async def _immediate(value):
    return value


@pytest.mark.asyncio
async def test_deactivate_payment_link_clears_without_stripe_configuration(monkeypatch):
    import app.routers.billing_extended as billing

    monkeypatch.setattr(billing.settings, "STRIPE_SECRET_KEY", "")
    invoice = SimpleNamespace(
        stripe_payment_link="https://pay.test", stripe_payment_link_id="plink_1"
    )
    await _deactivate_invoice_payment_link(invoice)
    assert invoice.stripe_payment_link is None
    assert invoice.stripe_payment_link_id is None


@pytest.mark.asyncio
async def test_deactivate_payment_link_handles_stripe_success_and_failure(monkeypatch):
    import stripe
    import app.routers.billing_extended as billing

    monkeypatch.setattr(billing.settings, "STRIPE_SECRET_KEY", "sk_test_key")
    calls = []

    def modify_success(link_id, **kwargs):
        calls.append((link_id, kwargs))

    monkeypatch.setattr(stripe.PaymentLink, "modify", modify_success)
    invoice = SimpleNamespace(
        stripe_payment_link="https://pay.test", stripe_payment_link_id="plink_1"
    )
    await _deactivate_invoice_payment_link(invoice)
    assert calls == [("plink_1", {"active": False})]

    def modify_failure(link_id, **kwargs):
        raise stripe.error.StripeError("stripe unavailable")

    monkeypatch.setattr(stripe.PaymentLink, "modify", modify_failure)
    invoice = SimpleNamespace(
        stripe_payment_link="https://pay.test", stripe_payment_link_id="plink_2"
    )
    await _deactivate_invoice_payment_link(invoice)
    assert invoice.stripe_payment_link is None
