"""Pydantic schemas for the matter/case management system."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

# ── Matter CRUD ───────────────────────────────────────────────────────────────


class MatterCreate(BaseModel):
    """Create a new matter. Only matter_name is required — all other fields optional."""

    matter_name: str = Field(..., max_length=500)
    description: str | None = None

    # Practice area / type
    matter_type: str | None = Field(None, max_length=100)
    practice_area: str | None = Field(None, max_length=200)

    # Litigation-specific (optional — for non-litigation matters leave blank)
    role: str | None = Field(None, max_length=100)
    counterparty: str | None = Field(None, max_length=500)
    jurisdiction: str | None = Field(None, max_length=300)

    source: str | None = Field(None, max_length=500)

    # People
    client_contact_id: str | None = None
    attorney_of_record_id: str | None = None
    assigned_user_ids: list[str] = []

    # Court / forum
    court: str | None = Field(None, max_length=300)
    judge: str | None = Field(None, max_length=200)
    case_number: str | None = Field(None, max_length=100)

    # Billing config
    billing_cycle: str = "monthly"
    billing_method: str = "hourly"
    hourly_rate: Decimal | None = None
    budget_amount: Decimal | None = None
    budget_currency: str = "USD"

    # Status / risk
    status: str = "open"
    risk_level: str | None = None
    stage: str | None = Field(None, max_length=200)
    key_dates: dict | None = None
    initial_posture: str | None = None

    # AI memory
    memory_content: str | None = None


class MatterUpdate(BaseModel):
    """Update an existing matter. All fields optional."""

    matter_name: str | None = Field(None, max_length=500)
    description: str | None = None
    matter_type: str | None = Field(None, max_length=100)
    role: str | None = Field(None, max_length=100)
    counterparty: str | None = Field(None, max_length=500)
    jurisdiction: str | None = Field(None, max_length=300)
    source: str | None = Field(None, max_length=500)
    practice_area: str | None = Field(None, max_length=200)
    status: str | None = Field(None, max_length=100)
    stage: str | None = Field(None, max_length=200)
    risk_level: str | None = Field(None, max_length=50)
    materiality: str | None = Field(None, max_length=50)
    exposure_range: str | None = Field(None, max_length=200)
    client_contact_id: str | None = None
    attorney_of_record_id: str | None = None
    court: str | None = Field(None, max_length=300)
    judge: str | None = Field(None, max_length=200)
    case_number: str | None = Field(None, max_length=100)
    billing_cycle: str | None = Field(None, max_length=50)
    billing_method: str | None = Field(None, max_length=50)
    hourly_rate: Decimal | None = None
    contingency_percentage: Decimal | None = None
    tax_rate: Decimal | None = None
    budget_amount: Decimal | None = None
    budget_currency: str | None = Field(None, max_length=3)
    budget_notification_threshold: Decimal | None = None
    key_dates: dict | None = None
    initial_posture: str | None = None
    outside_counsel: dict | None = None
    conflicts_status: str | None = Field(None, max_length=50)
    conflicts_override_reason: str | None = None
    legal_hold_issued: bool | None = None
    legal_hold_details: dict | None = None
    decision: str | None = Field(None, max_length=50)
    is_closed: bool | None = None
    outcome: str | None = Field(None, max_length=200)
    final_cost: str | None = Field(None, max_length=100)
    memory_content: str | None = None
    cloud_folder: dict | None = None


class MatterAssignmentResponse(BaseModel):
    """User assigned to a matter."""

    id: str
    user_id: str
    user_name: str
    role: str
    is_primary: bool
    assigned_at: datetime

    model_config = {"from_attributes": True}


class BudgetUtilization(BaseModel):
    """Budget vs actuals for a matter."""

    budget_amount: Decimal | None
    budget_currency: str
    total_hours: float
    total_billed: Decimal
    total_paid: Decimal
    total_unbilled: Decimal
    utilization_pct: float | None
    remaining: Decimal | None


class MatterResponse(BaseModel):
    """Full matter detail."""

    id: str
    slug: str
    matter_name: str
    description: str | None
    matter_type: str | None
    practice_area: str | None
    role: str | None
    counterparty: str | None
    jurisdiction: str | None
    status: str
    stage: str | None
    source: str | None
    risk_level: str | None
    materiality: str | None
    exposure_range: str | None
    conflicts_status: str
    conflicts_override_reason: str | None
    legal_hold_issued: bool
    legal_hold_details: dict | None
    key_dates: dict | None
    initial_posture: str | None
    decision: str | None
    is_closed: bool
    outcome: str | None
    final_cost: str | None
    outside_counsel: dict | None
    court: str | None
    judge: str | None
    case_number: str | None

    # Client
    client_contact_id: str | None
    client_name: str | None

    # Attorney of record
    attorney_of_record_id: str | None
    attorney_of_record_name: str | None

    # Billing
    budget_amount: Decimal | None
    budget_currency: str | None
    budget_notification_threshold: Decimal | None
    billing_cycle: str
    billing_method: str
    hourly_rate: Decimal | None
    contingency_percentage: Decimal | None
    tax_rate: Decimal | None

    # Assignments
    assignments: list[MatterAssignmentResponse] = []

    # Budget utilization (set separately)
    budget_utilization: BudgetUtilization | None = None

    # AI memory
    memory_content: str | None
    cloud_folder: dict | None

    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class MatterSummary(BaseModel):
    """Lightweight matter for portfolio lists."""

    id: str
    slug: str
    matter_name: str
    description: str | None
    matter_type: str | None
    practice_area: str | None
    status: str
    risk_level: str | None
    counterparty: str | None
    client_name: str | None
    attorney_of_record_name: str | None
    assigned_to: list[str] = []
    budget_amount: Decimal | None
    total_billed: Decimal
    budget_utilization_pct: float | None
    is_overdue: bool
    next_deadline: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class MatterListResponse(BaseModel):
    """Paginated matter list."""

    items: list[MatterSummary]
    total: int
    page: int
    page_size: int


# ── Assignments ───────────────────────────────────────────────────────────────


class MatterAssignmentCreate(BaseModel):
    """Assign a user to a matter."""

    user_id: str
    role: str = "associate"
    is_primary: bool = False


# ── Notes ─────────────────────────────────────────────────────────────────────


class MatterNoteCreate(BaseModel):
    """Create a note on a matter."""

    note_type: str = "internal"
    title: str = Field(..., max_length=500)
    content: str
    is_billable: bool = False
    hours: Decimal | None = None


class MatterNoteUpdate(BaseModel):
    """Update a note."""

    title: str | None = Field(None, max_length=500)
    content: str | None = None
    is_billable: bool | None = None
    hours: Decimal | None = None


class MatterNoteResponse(BaseModel):
    """Note detail."""

    id: str
    matter_id: str
    author_id: str | None
    author_name: str | None
    note_type: str
    title: str
    content: str
    is_billable: bool
    hours: Decimal | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── Memory ────────────────────────────────────────────────────────────────────


class MatterMemoryUpdate(BaseModel):
    """Update per-matter AI memory document."""

    content: str


class MatterMemoryResponse(BaseModel):
    """Per-matter AI memory document."""

    matter_id: str
    memory_content: str | None


# ── Retainers ─────────────────────────────────────────────────────────────────


class RetainerCreate(BaseModel):
    """Create a retainer for a matter."""

    contact_id: str
    retainer_type: str = "unearned"
    amount: Decimal
    minimum_balance: Decimal | None = None


class RetainerTransactionResponse(BaseModel):
    """Single transaction on a retainer."""

    id: str
    transaction_type: str
    amount: Decimal
    invoice_id: str | None
    description: str | None
    created_by: str
    created_at: datetime

    model_config = {"from_attributes": True}


class RetainerResponse(BaseModel):
    """Retainer detail with transactions."""

    id: str
    matter_id: str
    contact_id: str
    contact_name: str | None
    retainer_type: str
    amount: Decimal
    current_balance: Decimal
    minimum_balance: Decimal | None
    status: str
    transactions: list[RetainerTransactionResponse] = []
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── Retainer Drawdown ─────────────────────────────────────────────────────────


class RetainerDrawdownRequest(BaseModel):
    """Draw down from a retainer against an invoice."""

    amount: Decimal
    invoice_id: str | None = None
    description: str | None = None


# ── Timeline ──────────────────────────────────────────────────────────────────


class TimelineEntry(BaseModel):
    """Unified timeline entry — events, notes, time entries, docs."""

    entry_type: str  # event, note, time_entry, document
    id: str
    title: str
    content: str | None
    created_by: str | None
    created_by_name: str | None
    created_at: datetime
    metadata: dict | None = None

    model_config = {"from_attributes": True}


# ── Stats ─────────────────────────────────────────────────────────────────────


class MatterStats(BaseModel):
    """Aggregated matter statistics for the portfolio."""

    total: int
    by_status: dict[str, int]
    by_type: dict[str, int]
    by_practice_area: dict[str, int]
    by_risk: dict[str, int]
    active_legal_holds: int
    total_budget: Decimal | None
    total_billed: Decimal
    total_unbilled: Decimal
