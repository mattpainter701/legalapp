"""Pydantic schemas for the domestic relations module."""

import uuid as _uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


def _uuid_to_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    return str(value)


# ── Case ──────────────────────────────────────────────────────────────────────

CaseType = Literal[
    "divorce",
    "custody",
    "support",
    "paternity",
    "protective_order",
    "modification",
    "other",
]
CaseStatus = Literal["active", "draft", "pending", "closed"]


class DomesticCaseCreate(BaseModel):
    case_name: str
    case_type: CaseType = "support"
    jurisdiction: str = "ND"
    summary: Optional[str] = None
    county: Optional[str] = None
    court_name: Optional[str] = None
    case_number: Optional[str] = None
    filed_date: Optional[date] = None
    served_date: Optional[date] = None
    matter_id: Optional[str] = None
    client_contact_id: Optional[str] = None


class DomesticCaseUpdate(BaseModel):
    case_name: Optional[str] = None
    case_type: Optional[CaseType] = None
    status: Optional[CaseStatus] = None
    jurisdiction: Optional[str] = None
    summary: Optional[str] = None
    county: Optional[str] = None
    court_name: Optional[str] = None
    case_number: Optional[str] = None
    filed_date: Optional[date] = None
    served_date: Optional[date] = None
    matter_id: Optional[str] = None
    client_contact_id: Optional[str] = None


class DomesticCaseResponse(BaseModel):
    id: str
    case_name: str
    case_type: str
    status: str
    jurisdiction: str
    summary: Optional[str]
    county: Optional[str]
    court_name: Optional[str]
    case_number: Optional[str]
    filed_date: Optional[date]
    served_date: Optional[date]
    matter_id: Optional[str]
    client_contact_id: Optional[str]
    client_name: Optional[str] = None
    children_count: int = 0
    parties_count: int = 0
    next_deadline: Optional[date] = None
    current_support_amount: Optional[Decimal] = None
    created_at: datetime
    updated_at: datetime


class DomesticCaseStats(BaseModel):
    total: int
    active: int
    draft: int
    pending: int
    closed: int
    total_children: int
    open_orders: int
    upcoming_deadlines: int


# ── Party ─────────────────────────────────────────────────────────────────────

PartyRole = Literal[
    "petitioner", "respondent", "parent_a", "parent_b", "guardian", "other"
]


class PartyBase(BaseModel):
    name: str
    role: PartyRole = "respondent"
    contact_id: Optional[str] = None
    is_client: bool = False
    gross_monthly_income: Optional[Decimal] = Field(default=None, ge=0)
    federal_income_tax: Optional[Decimal] = Field(default=None, ge=0)
    state_income_tax: Optional[Decimal] = Field(default=None, ge=0)
    fica_tax: Optional[Decimal] = Field(default=None, ge=0)
    required_retirement: Decimal = Decimal("0")
    union_dues: Decimal = Decimal("0")
    health_insurance_children: Decimal = Decimal("0")
    existing_support_paid: Decimal = Decimal("0")
    other_children_in_home: int = Field(default=0, ge=0)
    is_imputed: bool = False
    imputed_basis: Optional[str] = None
    annual_overnights: int = Field(default=0, ge=0, le=366)
    email: Optional[str] = None
    phone: Optional[str] = None
    notes: Optional[str] = None


class PartyCreate(PartyBase):
    pass


class PartyUpdate(BaseModel):
    name: Optional[str] = None
    role: Optional[PartyRole] = None
    contact_id: Optional[str] = None
    is_client: Optional[bool] = None
    gross_monthly_income: Optional[Decimal] = Field(default=None, ge=0)
    federal_income_tax: Optional[Decimal] = Field(default=None, ge=0)
    state_income_tax: Optional[Decimal] = Field(default=None, ge=0)
    fica_tax: Optional[Decimal] = Field(default=None, ge=0)
    required_retirement: Optional[Decimal] = None
    union_dues: Optional[Decimal] = None
    health_insurance_children: Optional[Decimal] = None
    existing_support_paid: Optional[Decimal] = None
    other_children_in_home: Optional[int] = Field(default=None, ge=0)
    is_imputed: Optional[bool] = None
    imputed_basis: Optional[str] = None
    annual_overnights: Optional[int] = Field(default=None, ge=0, le=366)
    email: Optional[str] = None
    phone: Optional[str] = None
    notes: Optional[str] = None


class PartyResponse(PartyBase):
    id: str
    case_id: str
    created_at: datetime
    model_config = {"from_attributes": True}

    @field_validator("id", "case_id", "contact_id", mode="before")
    @classmethod
    def _coerce(cls, v: str | _uuid.UUID | None) -> Optional[str]:
        return _uuid_to_str(v)


# ── Child ─────────────────────────────────────────────────────────────────────


class ChildCreate(BaseModel):
    name: str
    date_of_birth: Optional[date] = None
    primary_residence_party_id: Optional[str] = None
    has_special_needs: bool = False
    notes: Optional[str] = None


class ChildUpdate(BaseModel):
    name: Optional[str] = None
    date_of_birth: Optional[date] = None
    primary_residence_party_id: Optional[str] = None
    has_special_needs: Optional[bool] = None
    notes: Optional[str] = None


class ChildResponse(BaseModel):
    id: str
    case_id: str
    name: str
    date_of_birth: Optional[date]
    primary_residence_party_id: Optional[str]
    has_special_needs: bool
    notes: Optional[str]
    created_at: datetime
    model_config = {"from_attributes": True}

    @field_validator(
        "id", "case_id", "primary_residence_party_id", mode="before"
    )
    @classmethod
    def _coerce(cls, v: str | _uuid.UUID | None) -> Optional[str]:
        return _uuid_to_str(v)


# ── Custody ───────────────────────────────────────────────────────────────────

CalcCustodyType = Literal["primary", "equal", "split"]


class CustodyCreate(BaseModel):
    legal_custody: str = "joint"
    physical_custody: str = "primary"
    calc_custody_type: CalcCustodyType = "primary"
    primary_party_id: Optional[str] = None
    children_with_party_a: int = Field(default=0, ge=0)
    schedule_description: Optional[str] = None
    effective_date: Optional[date] = None
    notes: Optional[str] = None


class CustodyUpdate(BaseModel):
    legal_custody: Optional[str] = None
    physical_custody: Optional[str] = None
    calc_custody_type: Optional[CalcCustodyType] = None
    primary_party_id: Optional[str] = None
    children_with_party_a: Optional[int] = Field(default=None, ge=0)
    schedule_description: Optional[str] = None
    effective_date: Optional[date] = None
    notes: Optional[str] = None


class CustodyResponse(BaseModel):
    id: str
    case_id: str
    legal_custody: str
    physical_custody: str
    calc_custody_type: str
    primary_party_id: Optional[str]
    children_with_party_a: int
    schedule_description: Optional[str]
    effective_date: Optional[date]
    notes: Optional[str]
    created_at: datetime
    model_config = {"from_attributes": True}

    @field_validator("id", "case_id", "primary_party_id", mode="before")
    @classmethod
    def _coerce(cls, v: str | _uuid.UUID | None) -> Optional[str]:
        return _uuid_to_str(v)


# ── Support order + payments ──────────────────────────────────────────────────


class SupportOrderCreate(BaseModel):
    obligor_party_id: Optional[str] = None
    obligee_party_id: Optional[str] = None
    calculation_id: Optional[str] = None
    monthly_amount: Decimal = Decimal("0")
    frequency: Literal["monthly", "semimonthly", "biweekly", "weekly"] = "monthly"
    effective_date: Optional[date] = None
    end_date: Optional[date] = None
    arrears_balance: Decimal = Decimal("0")
    status: Literal["proposed", "entered", "active", "modified", "terminated"] = (
        "proposed"
    )
    order_type: Literal["child_support", "spousal_support", "medical", "other"] = (
        "child_support"
    )
    notes: Optional[str] = None


class SupportOrderUpdate(BaseModel):
    obligor_party_id: Optional[str] = None
    obligee_party_id: Optional[str] = None
    calculation_id: Optional[str] = None
    monthly_amount: Optional[Decimal] = None
    frequency: Optional[
        Literal["monthly", "semimonthly", "biweekly", "weekly"]
    ] = None
    effective_date: Optional[date] = None
    end_date: Optional[date] = None
    arrears_balance: Optional[Decimal] = None
    status: Optional[
        Literal["proposed", "entered", "active", "modified", "terminated"]
    ] = None
    order_type: Optional[
        Literal["child_support", "spousal_support", "medical", "other"]
    ] = None
    notes: Optional[str] = None


class SupportOrderResponse(BaseModel):
    id: str
    case_id: str
    obligor_party_id: Optional[str]
    obligee_party_id: Optional[str]
    calculation_id: Optional[str]
    monthly_amount: Decimal
    frequency: str
    effective_date: Optional[date]
    end_date: Optional[date]
    arrears_balance: Decimal
    status: str
    order_type: str
    notes: Optional[str]
    total_paid: Decimal = Decimal("0")
    created_at: datetime
    model_config = {"from_attributes": True}

    @field_validator(
        "id",
        "case_id",
        "obligor_party_id",
        "obligee_party_id",
        "calculation_id",
        mode="before",
    )
    @classmethod
    def _coerce(cls, v: str | _uuid.UUID | None) -> Optional[str]:
        return _uuid_to_str(v)


class PaymentCreate(BaseModel):
    payment_date: date
    amount: Decimal = Decimal("0")
    applied_to_current: Optional[Decimal] = None
    applied_to_arrears: Optional[Decimal] = None
    method: Optional[str] = None
    reference_number: Optional[str] = None
    notes: Optional[str] = None


class PaymentResponse(BaseModel):
    id: str
    case_id: str
    order_id: str
    payment_date: date
    amount: Decimal
    applied_to_current: Decimal
    applied_to_arrears: Decimal
    method: Optional[str]
    reference_number: Optional[str]
    notes: Optional[str]
    created_at: datetime
    model_config = {"from_attributes": True}

    @field_validator("id", "case_id", "order_id", mode="before")
    @classmethod
    def _coerce(cls, v: str | _uuid.UUID | None) -> Optional[str]:
        return _uuid_to_str(v)


# ── Deadlines + events ────────────────────────────────────────────────────────


class DeadlineCreate(BaseModel):
    title: str
    deadline_type: Literal[
        "hearing", "filing", "exchange", "discovery", "mediation", "review", "other"
    ] = "other"
    due_date: date
    status: Literal["pending", "in_progress", "complete", "overdue", "na"] = "pending"
    assigned_to: Optional[str] = None
    notes: Optional[str] = None


class DeadlineUpdate(BaseModel):
    title: Optional[str] = None
    deadline_type: Optional[
        Literal[
            "hearing", "filing", "exchange", "discovery", "mediation", "review", "other"
        ]
    ] = None
    due_date: Optional[date] = None
    status: Optional[
        Literal["pending", "in_progress", "complete", "overdue", "na"]
    ] = None
    assigned_to: Optional[str] = None
    notes: Optional[str] = None


class DeadlineResponse(BaseModel):
    id: str
    case_id: str
    title: str
    deadline_type: str
    due_date: date
    status: str
    assigned_to: Optional[str]
    completed_at: Optional[datetime]
    notes: Optional[str]
    created_at: datetime
    model_config = {"from_attributes": True}

    @field_validator("id", "case_id", "assigned_to", mode="before")
    @classmethod
    def _coerce(cls, v: str | _uuid.UUID | None) -> Optional[str]:
        return _uuid_to_str(v)


class EventCreate(BaseModel):
    event_type: str = "note"
    title: str
    content: Optional[str] = None


class EventResponse(BaseModel):
    id: str
    case_id: str
    event_type: str
    title: str
    content: Optional[str]
    created_at: datetime
    model_config = {"from_attributes": True}

    @field_validator("id", "case_id", mode="before")
    @classmethod
    def _coerce(cls, v: str | _uuid.UUID | None) -> Optional[str]:
        return _uuid_to_str(v)


# ── Calculator (engine bridge) ────────────────────────────────────────────────


class CalcParent(BaseModel):
    role: str = "parent_a"
    name: Optional[str] = None
    gross_monthly_income: Decimal = Field(default=Decimal("0"), ge=0)
    federal_income_tax: Optional[Decimal] = Field(default=None, ge=0)
    state_income_tax: Optional[Decimal] = Field(default=None, ge=0)
    fica_tax: Optional[Decimal] = Field(default=None, ge=0)
    required_retirement: Decimal = Decimal("0")
    union_dues: Decimal = Decimal("0")
    health_insurance_children: Decimal = Decimal("0")
    existing_support_paid: Decimal = Decimal("0")
    other_children_in_home: int = Field(default=0, ge=0)
    is_imputed: bool = False
    imputed_basis: Optional[str] = None
    annual_overnights: int = Field(default=0, ge=0, le=366)


class CalcRequest(BaseModel):
    jurisdiction: str = "ND"
    num_children: int = Field(ge=0)
    parents: List[CalcParent]
    effective_date: Optional[date] = None
    custody_type: CalcCustodyType = "primary"
    obligor_role: Optional[str] = None
    children_with_parent_a: int = Field(default=0, ge=0)
    deviation_amount: Optional[Decimal] = None
    deviation_reason: Optional[str] = None
    allow_estimates: bool = True


class WorksheetLineResponse(BaseModel):
    code: str
    label: str
    amount: Optional[str]
    detail: Optional[str]
    estimated: bool


class WorksheetResponse(BaseModel):
    jurisdiction: str
    state_name: str
    model_type: str
    schedule_version: str
    effective_date: str
    num_children: int
    obligor_role: Optional[str]
    presumptive_amount: str
    final_amount: str
    deviation_amount: Optional[str]
    deviation_reason: Optional[str]
    lines: List[WorksheetLineResponse]
    warnings: List[str]
    citations: List[str]


class CalculationSaveRequest(BaseModel):
    label: Optional[str] = None
    is_final: bool = False
    request: CalcRequest


class CalculationResponse(BaseModel):
    id: str
    case_id: str
    label: Optional[str]
    jurisdiction: str
    model_type: Optional[str]
    schedule_version: Optional[str]
    effective_date: Optional[date]
    num_children: int
    obligor_role: Optional[str]
    presumptive_amount: Decimal
    final_amount: Decimal
    deviation_amount: Optional[Decimal]
    deviation_reason: Optional[str]
    worksheet: dict
    input_snapshot: dict
    is_final: bool
    created_at: datetime
    model_config = {"from_attributes": True}

    @field_validator("id", "case_id", mode="before")
    @classmethod
    def _coerce(cls, v: str | _uuid.UUID | None) -> Optional[str]:
        return _uuid_to_str(v)


class CalculationListItem(BaseModel):
    id: str
    case_id: str
    label: Optional[str]
    jurisdiction: str
    num_children: int
    obligor_role: Optional[str]
    presumptive_amount: Decimal
    final_amount: Decimal
    is_final: bool
    created_at: datetime
    model_config = {"from_attributes": True}

    @field_validator("id", "case_id", mode="before")
    @classmethod
    def _coerce(cls, v: str | _uuid.UUID | None) -> Optional[str]:
        return _uuid_to_str(v)


class JurisdictionInfo(BaseModel):
    state_code: str
    state_name: str
    model_type: str
    schedule_version: str
    effective_date: str
    verified: bool
