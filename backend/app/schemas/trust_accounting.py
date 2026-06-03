"""Pydantic schemas for IOLTA trust accounting — accounts, transactions, reconciliation."""

from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field


# ── Trust Account ────────────────────────────────────────────────────────────


class TrustAccountCreate(BaseModel):
    matter_id: str
    account_name: str = Field(..., min_length=1, max_length=300)
    bank_name: Optional[str] = None
    account_number_masked: Optional[str] = Field(
        default=None, max_length=10, description="Last 4 digits only"
    )
    minimum_balance: Optional[Decimal] = Field(default=None, ge=0)
    auto_replenish_enabled: bool = False
    auto_replenish_amount: Optional[Decimal] = Field(default=None, ge=0)
    notes: Optional[str] = None


class TrustAccountUpdate(BaseModel):
    account_name: Optional[str] = None
    bank_name: Optional[str] = None
    account_number_masked: Optional[str] = None
    minimum_balance: Optional[Decimal] = Field(default=None, ge=0)
    auto_replenish_enabled: Optional[bool] = None
    auto_replenish_amount: Optional[Decimal] = Field(default=None, ge=0)
    is_active: Optional[bool] = None
    notes: Optional[str] = None


class TrustAccountResponse(BaseModel):
    id: str
    tenant_id: str
    matter_id: str
    account_name: str
    bank_name: Optional[str] = None
    account_number_masked: Optional[str] = None
    current_balance: Decimal
    minimum_balance: Optional[Decimal] = None
    auto_replenish_enabled: bool
    auto_replenish_amount: Optional[Decimal] = None
    is_active: bool
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TrustAccountListResponse(BaseModel):
    items: list[TrustAccountResponse]
    total: int
    total_balance: Decimal


# ── Trust Transaction ─────────────────────────────────────────────────────────


class TrustTransactionCreate(BaseModel):
    trust_account_id: str
    transaction_type: str = Field(
        ...,
        description="deposit, disbursement, transfer_in, transfer_out, replenishment, fee, adjustment",
    )
    amount: Decimal = Field(..., gt=0)
    description: str = Field(..., min_length=1)
    transaction_date: Optional[date] = None
    reference_number: Optional[str] = None
    check_number: Optional[str] = None
    notes: Optional[str] = None


class TrustTransactionResponse(BaseModel):
    id: str
    tenant_id: str
    trust_account_id: str
    transaction_type: str
    amount: Decimal
    description: str
    transaction_date: date
    reference_number: Optional[str] = None
    check_number: Optional[str] = None
    is_reconciled: bool
    reconciled_at: Optional[datetime] = None
    notes: Optional[str] = None
    created_by: str
    created_at: datetime

    model_config = {"from_attributes": True}


class TrustTransactionListResponse(BaseModel):
    items: list[TrustTransactionResponse]
    total: int
    total_deposits: Decimal
    total_disbursements: Decimal
    net_change: Decimal


# ── Reconciliation ────────────────────────────────────────────────────────────


class ReconciliationRequest(BaseModel):
    trust_account_id: str
    bank_balance: Decimal = Field(..., description="Balance per bank statement")
    as_of_date: Optional[date] = None
    outstanding_deposits: Decimal = Field(
        default=0, description="Deposits not yet on bank statement"
    )
    outstanding_disbursements: Decimal = Field(
        default=0, description="Disbursements not yet cleared"
    )
    notes: Optional[str] = None


class ReconciliationLine(BaseModel):
    """One reconciling item."""

    description: str
    amount: Decimal
    is_outstanding: bool = False


class ReconciliationResponse(BaseModel):
    trust_account_id: str
    as_of_date: date
    # Three components of IOLTA three-way reconciliation
    bank_balance: Decimal
    trust_liability: Decimal = Field(
        ..., description="Sum of all trust account balances (should equal bank)"
    )
    unallocated: Decimal = Field(
        ...,
        description="Funds received but not yet allocated to a matter (should be zero)",
    )
    # Adjustments
    outstanding_deposits: Decimal
    outstanding_disbursements: Decimal
    adjusted_bank_balance: Decimal = Field(
        ...,
        description="bank_balance + outstanding_deposits - outstanding_disbursements",
    )
    # Results
    is_reconciled: bool = Field(
        ...,
        description="True when adjusted_bank_balance == trust_liability + unallocated",
    )
    difference: Decimal = Field(
        ..., description="adjusted_bank_balance - (trust_liability + unallocated)"
    )
    # Details
    reconciling_items: list[ReconciliationLine] = []
    notes: Optional[str] = None
    reconciled_at: Optional[datetime] = None
