"""Pydantic schemas for legal billing: time entries, expenses, invoices, payments."""

import uuid as _uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field, field_serializer, field_validator


# ── Time Entries ────────────────────────────────────────────────────────────


class TimeEntryCreate(BaseModel):
    matter_id: str
    description: str = Field(..., min_length=1)
    hours: Decimal = Field(..., gt=0)
    hourly_rate: Optional[Decimal] = Field(default=None, gt=0)
    date: date
    is_billable: bool = True
    utbms_task_code: Optional[str] = None
    utbms_activity_code: Optional[str] = None


class TimeEntryUpdate(BaseModel):
    description: Optional[str] = None
    hours: Optional[Decimal] = Field(default=None, gt=0)
    hourly_rate: Optional[Decimal] = Field(default=None, gt=0)
    date: Optional[date] = None
    is_billable: Optional[bool] = None
    utbms_task_code: Optional[str] = None
    utbms_activity_code: Optional[str] = None
    status: Optional[str] = None


class TimeEntryResponse(BaseModel):
    id: str
    tenant_id: str
    matter_id: str
    user_id: str
    description: str
    hours: Decimal
    hourly_rate: Decimal
    amount: Decimal
    date: date
    is_billable: bool
    utbms_task_code: Optional[str] = None
    utbms_activity_code: Optional[str] = None
    invoice_id: Optional[str] = None
    status: str
    timer_started_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @field_validator(
        "id", "tenant_id", "matter_id", "user_id", "invoice_id", mode="before"
    )
    @classmethod
    def _coerce_uuid(cls, value: str | _uuid.UUID | None) -> str | None:
        """Coerce UUID objects to strings during validation (from_attributes)."""
        return str(value) if value is not None else None

    @field_serializer("id", "tenant_id", "matter_id", "user_id", "invoice_id")
    def _str_uuid(self, value: str | _uuid.UUID | None, _info: object) -> str | None:
        return str(value) if value is not None else None


class TimeEntryListResponse(BaseModel):
    items: list[TimeEntryResponse]
    total: int
    total_hours: Decimal
    total_amount: Decimal


class TimerStartRequest(BaseModel):
    matter_id: str
    description: str = Field(default="", max_length=4000)
    is_billable: bool = True
    hourly_rate: Optional[Decimal] = Field(default=None, gt=0)


class TimerStopRequest(BaseModel):
    description: Optional[str] = Field(default=None, max_length=4000)


# ── Expenses ─────────────────────────────────────────────────────────────────


class ExpenseCreate(BaseModel):
    matter_id: str
    description: str = Field(..., min_length=1)
    amount: Decimal = Field(..., gt=0)
    date: date
    category: str = "other"
    vendor: Optional[str] = None
    is_billable: bool = True


class ExpenseUpdate(BaseModel):
    description: Optional[str] = None
    amount: Optional[Decimal] = Field(default=None, gt=0)
    date: Optional[date] = None
    category: Optional[str] = None
    vendor: Optional[str] = None
    is_billable: Optional[bool] = None


class ExpenseResponse(BaseModel):
    id: str
    tenant_id: str
    matter_id: str
    user_id: str
    description: str
    amount: Decimal
    date: date
    category: str
    vendor: Optional[str] = None
    is_billable: bool
    invoice_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @field_validator(
        "id", "tenant_id", "matter_id", "user_id", "invoice_id", mode="before"
    )
    @classmethod
    def _coerce_uuid(cls, value: str | _uuid.UUID | None) -> str | None:
        return str(value) if value is not None else None


class ExpenseListResponse(BaseModel):
    items: list[ExpenseResponse]
    total: int
    total_amount: Decimal


# ── Invoices ─────────────────────────────────────────────────────────────────


class InvoiceLineItemCreate(BaseModel):
    source_type: str  # time_entry, expense, flat_fee, adjustment, discount
    source_id: Optional[str] = None
    description: str = Field(..., min_length=1)
    quantity: Decimal = Field(default=1, ge=0)
    unit_price: Decimal = Field(..., ge=0)
    amount: Decimal = Field(..., ge=0)
    sort_order: int = 0


class InvoiceLineItemResponse(BaseModel):
    id: str
    invoice_id: str
    source_type: str
    source_id: Optional[str] = None
    description: str
    quantity: Decimal
    unit_price: Decimal
    amount: Decimal
    sort_order: int
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("id", "invoice_id", "source_id", mode="before")
    @classmethod
    def _coerce_uuid(cls, value: str | _uuid.UUID | None) -> str | None:
        return str(value) if value is not None else None


class InvoiceCreate(BaseModel):
    matter_id: str
    issue_date: date
    due_date: date
    notes: Optional[str] = None
    payment_terms: str = "Net 30"
    line_items: list[InvoiceLineItemCreate] = []


class InvoiceUpdate(BaseModel):
    issue_date: Optional[date] = None
    due_date: Optional[date] = None
    notes: Optional[str] = None
    payment_terms: Optional[str] = None
    status: Optional[str] = None


class InvoiceResponse(BaseModel):
    id: str
    tenant_id: str
    matter_id: str
    invoice_number: str
    status: str
    issue_date: date
    due_date: date
    subtotal: Decimal
    tax_amount: Decimal
    total: Decimal
    notes: Optional[str] = None
    payment_terms: Optional[str] = None
    stripe_payment_link: Optional[str] = None
    qbo_invoice_id: Optional[str] = None
    qbo_sync_status: str
    ledes_exported_at: Optional[datetime] = None
    retainer_id: Optional[str] = None
    billing_period_start: Optional[date] = None
    billing_period_end: Optional[date] = None
    sent_at: Optional[datetime] = None
    amount_paid: Decimal = Decimal("0")
    balance_due: Decimal = Decimal("0")
    is_overdue: bool = False
    matter_name: Optional[str] = None
    created_by: str
    line_items: list[InvoiceLineItemResponse] = []
    payments: list["PaymentResponse"] = []
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @field_validator(
        "id", "tenant_id", "matter_id", "retainer_id", "created_by", mode="before"
    )
    @classmethod
    def _coerce_uuid(cls, value: str | _uuid.UUID | None) -> str | None:
        return str(value) if value is not None else None


class InvoiceListResponse(BaseModel):
    items: list[InvoiceResponse]
    total: int
    total_amount: Decimal


class GenerateInvoiceRequest(BaseModel):
    """Request to generate an invoice from unbilled time entries and expenses."""

    matter_id: str
    issue_date: Optional[date] = None
    due_date_days: int = 30
    notes: Optional[str] = None
    payment_terms: str = "Net 30"
    tax_rate: Optional[Decimal] = Field(default=None, ge=0, le=1)


# ── Payments ─────────────────────────────────────────────────────────────────


class PaymentCreate(BaseModel):
    invoice_id: str
    amount: Decimal = Field(..., gt=0)
    payment_date: date
    method: str = "other"
    reference_number: Optional[str] = None
    notes: Optional[str] = None


class PaymentResponse(BaseModel):
    id: str
    tenant_id: str
    invoice_id: str
    amount: Decimal
    payment_date: date
    method: str
    reference_number: Optional[str] = None
    stripe_payment_intent_id: Optional[str] = None
    qbo_payment_id: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("id", "tenant_id", "invoice_id", mode="before")
    @classmethod
    def _coerce_uuid(cls, value: str | _uuid.UUID | None) -> str | None:
        return str(value) if value is not None else None


# ── Stripe Payment Link ──────────────────────────────────────────────────────


class StripePaymentLinkRequest(BaseModel):
    invoice_id: str


class StripePaymentLinkResponse(BaseModel):
    invoice_id: str
    payment_link_url: str
    payment_link_id: str


# ── Invoice Export ───────────────────────────────────────────────────────────


class InvoiceExportRequest(BaseModel):
    format: str = "csv"  # csv, pdf, ledes1998b


# ── Billing Settings ─────────────────────────────────────────────────────────


class BillingSettingsResponse(BaseModel):
    default_hourly_rate: Optional[Decimal] = None
    time_rounding_minutes: int = 6


class BillingSettingsUpdate(BaseModel):
    default_hourly_rate: Optional[Decimal] = Field(default=None, gt=0)
    time_rounding_minutes: Optional[int] = Field(default=None, ge=1, le=60)
