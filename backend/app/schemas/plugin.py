"""Pydantic schemas for the legal practice plugin system."""

from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel


# ── Practice Profiles ─────────────────────────────────────────────────────────


class PracticeProfileUpsert(BaseModel):
    profile_content: str
    is_complete: bool = False


class PracticeProfileResponse(BaseModel):
    id: str
    plugin_name: str
    profile_content: str
    is_complete: bool
    setup_step: int
    updated_at: datetime

    class Config:
        from_attributes = True


# ── Structured Plugin Setup ──────────────────────────────────────────────────


class PluginSetupUpsert(BaseModel):
    jurisdictions: list[str] = []
    escalation_rules: dict = {}
    approval_thresholds: dict = {}
    template_preferences: dict = {}
    cloud_bindings: dict = {}
    calendar_bindings: dict = {}
    house_style: dict = {}
    custom_config: dict = {}
    generated_profile: Optional[str] = None
    is_complete: bool = False


class PluginSetupHealth(BaseModel):
    setup_status: str
    missing_required_fields: List[str] = []
    missing_required_integrations: List[str] = []
    available_integrations: List[str] = []
    optional_integrations: List[str] = []
    warnings: List[str] = []


class PluginSetupResponse(BaseModel):
    plugin_name: str
    display_name: str
    setup: Optional[PluginSetupUpsert] = None
    health: PluginSetupHealth
    updated_at: Optional[datetime] = None


class PluginEntitlementUpdate(BaseModel):
    status: str
    source: Optional[str] = None
    seat_limit: Optional[int] = None
    config: dict = {}
    expires_at: Optional[datetime] = None


class PluginEntitlementResponse(BaseModel):
    plugin_name: str
    status: str
    source: Optional[str] = None
    seat_limit: Optional[int] = None
    config: dict = {}
    expires_at: Optional[datetime] = None
    updated_at: datetime


# ── Plugin Skill Execution ────────────────────────────────────────────────────


class SkillRequest(BaseModel):
    skill: str  # e.g. "vendor-agreement-review"
    input_text: str  # Contract text, question, etc.
    context: Optional[dict] = None  # Extra structured context
    matter_id: Optional[str] = None
    use_premium: bool = False


class CitationTag(BaseModel):
    text: str
    tag: str  # "settled", "verify", "model-knowledge", "web-search"
    source: Optional[str] = None


class SkillFinding(BaseModel):
    category: str
    legal_risk: str  # "critical", "high", "medium", "low"
    business_friction: str  # "critical", "high", "medium", "low"
    finding: str
    redline: Optional[str] = None
    fallback: Optional[str] = None
    citations: List[CitationTag] = []
    requires_verify: bool = False


class SkillResponse(BaseModel):
    skill: str
    plugin: str
    memo: str  # Full formatted markdown output with work-product header
    findings: List[SkillFinding] = []
    gates_triggered: List[str] = []  # Hard gate messages
    flags: List[str] = []  # Soft warning flags
    requires_attorney_review: bool = True
    tokens_used: int = 0
    model_used: str


# ── Matters ───────────────────────────────────────────────────────────────────


class MatterCreate(BaseModel):
    matter_name: str
    matter_type: str
    counterparty: str
    jurisdiction: str
    role: str
    source: str


class MatterResponse(BaseModel):
    id: str
    slug: str
    matter_name: str
    matter_type: str
    role: str
    counterparty: str
    jurisdiction: str
    status: str
    risk_level: Optional[str]
    materiality: Optional[str]
    conflicts_status: str
    legal_hold_issued: bool
    is_closed: bool
    budget_amount: Optional[float] = None
    budget_currency: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class MatterUpdate(BaseModel):
    matter_name: Optional[str] = None
    matter_type: Optional[str] = None
    counterparty: Optional[str] = None
    jurisdiction: Optional[str] = None
    role: Optional[str] = None
    source: Optional[str] = None
    status: Optional[str] = None
    stage: Optional[str] = None
    risk_level: Optional[str] = None
    materiality: Optional[str] = None
    exposure_range: Optional[str] = None
    outside_counsel: Optional[dict] = None
    internal_owners: Optional[dict] = None
    conflicts_status: Optional[str] = None
    conflicts_override_reason: Optional[str] = None
    legal_hold_issued: Optional[bool] = None
    legal_hold_details: Optional[dict] = None
    key_dates: Optional[dict] = None
    initial_posture: Optional[str] = None
    decision: Optional[str] = None
    is_closed: Optional[bool] = None
    outcome: Optional[str] = None
    final_cost: Optional[str] = None
    budget_amount: Optional[float] = None
    budget_currency: Optional[str] = None


class MatterEventCreate(BaseModel):
    event_type: str
    title: str
    content: str


class MatterEventResponse(BaseModel):
    id: str
    event_type: str
    title: str
    content: str
    created_at: datetime

    class Config:
        from_attributes = True


# ── Renewals ──────────────────────────────────────────────────────────────────


class RenewalCreate(BaseModel):
    contract_name: str
    vendor: str
    renewal_date: date
    notice_deadline: Optional[date] = None
    contract_value_annual: Optional[float] = None
    auto_renewal: bool = True
    business_owner: Optional[str] = None
    business_owner_email: Optional[str] = None
    notes: Optional[str] = None


class RenewalResponse(BaseModel):
    id: str
    contract_name: str
    vendor: str
    renewal_date: date
    notice_deadline: Optional[date]
    contract_value_annual: Optional[float]
    auto_renewal: bool
    status: str
    days_until_renewal: int
    urgency: str
    created_at: datetime

    class Config:
        from_attributes = True


class RenewalUpdate(BaseModel):
    status: Optional[str] = None
    notes: Optional[str] = None


# ── Estates (Trust & Estate) ──────────────────────────────────────────────────


class EstateCreate(BaseModel):
    title: str
    grantor: Optional[str] = None
    estate_type: Optional[str] = None
    summary: Optional[str] = None


class EstateUpdate(BaseModel):
    title: Optional[str] = None
    grantor: Optional[str] = None
    estate_type: Optional[str] = None
    status: Optional[str] = None
    summary: Optional[str] = None


class EstateEventCreate(BaseModel):
    event_type: str
    title: str
    content: Optional[str] = None


class EstateEventResponse(BaseModel):
    id: str
    event_type: str
    title: str
    content: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class EstateResponse(BaseModel):
    id: str
    title: str
    grantor: Optional[str]
    estate_type: Optional[str]
    status: str
    summary: Optional[str]
    created_at: datetime
    updated_at: datetime
    events: List[EstateEventResponse] = []

    class Config:
        from_attributes = True


# ── Mediation Cases ───────────────────────────────────────────────────────────


class MediationCaseCreate(BaseModel):
    title: str
    parties: Optional[str] = None
    summary: Optional[str] = None


class MediationCaseUpdate(BaseModel):
    title: Optional[str] = None
    parties: Optional[str] = None
    status: Optional[str] = None
    summary: Optional[str] = None


class MediationCaseEventCreate(BaseModel):
    event_type: str
    title: str
    content: Optional[str] = None


class MediationCaseEventResponse(BaseModel):
    id: str
    event_type: str
    title: str
    content: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class MediationCaseResponse(BaseModel):
    id: str
    title: str
    parties: Optional[str]
    status: str
    summary: Optional[str]
    created_at: datetime
    updated_at: datetime
    events: List[MediationCaseEventResponse] = []

    class Config:
        from_attributes = True


# ── Plugin Listing ────────────────────────────────────────────────────────────


class PluginInfo(BaseModel):
    id: str
    plugin_id: str
    name: str
    plugin_name: str
    display_name: str
    category: str
    description: str
    skills: List[str]
    matter_types: List[str] = []
    primary_route: Optional[str] = None
    required_integrations: List[str] = []
    optional_integrations: List[str] = []
    available_integrations: List[str] = []
    missing_required_integrations: List[str] = []
    supports_matter_assignment: bool = True
    setup_required: bool = True
    entitlement_status: str = "available"
    is_purchased: bool = False
    is_trial: bool = False
    is_locked: bool = False
    setup_status: str = "not-started"
    has_profile: bool
    profile_is_complete: bool


class PluginListResponse(BaseModel):
    plugins: List[PluginInfo]


# ── Prompt Management (admin) ────────────────────────────────────────────────


class PromptInfo(BaseModel):
    """Metadata for one prompt (without content — for tree listing)."""

    plugin_name: str
    skill_name: str
    has_override: bool
    is_active: bool = True
    updated_at: Optional[datetime] = None


class PromptPluginTree(BaseModel):
    """A plugin entry in the prompt tree listing."""

    plugin_name: str
    display_name: str
    skills: List[PromptInfo]


class PromptListResponse(BaseModel):
    plugins: List[PromptPluginTree]


class PromptDetail(BaseModel):
    """Full prompt detail including default and override content."""

    plugin_name: str
    skill_name: str
    default_content: str
    override_content: Optional[str] = None
    is_active: bool = True
    updated_at: Optional[datetime] = None


class PromptUpdate(BaseModel):
    """Create or update a prompt override."""

    prompt_content: str
    is_active: bool = True


class PromptTestRequest(BaseModel):
    """Test a prompt with sample input."""

    prompt_content: str
    sample_input: str
    context: Optional[dict] = None


class PromptTestResponse(BaseModel):
    """Response from a prompt test execution."""

    response_text: str
    tokens_used: int
    model_used: str
    gates_triggered: List[str] = []


class PromptResetResponse(BaseModel):
    """Result of resetting a prompt override."""

    plugin_name: str
    skill_name: str
    restored: bool
