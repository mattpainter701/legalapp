from datetime import datetime
from uuid import UUID
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, field_serializer


class UserDetailResponse(BaseModel):
    """Full user profile with all fields."""

    id: str
    email: str
    full_name: Optional[str] = None
    role: str
    is_active: bool
    # Enhanced user fields
    practice_areas: Optional[List[str]] = None
    expertise_level: str = "mid"
    default_skill: Optional[str] = None
    privacy_mode: bool = False
    workspace_mcp_enabled: bool = True
    professional_role: Optional[str] = None
    job_title: Optional[str] = None
    office_location: Optional[str] = None
    primary_jurisdictions: Optional[List[str]] = None
    memory_summary: Optional[str] = None
    last_memory_update: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class UserRoleSummary(BaseModel):
    id: str
    name: str


class UserResponse(BaseModel):
    id: str
    email: str
    full_name: Optional[str] = None
    role: str
    role_ids: list[str] = Field(default_factory=list)
    roles: list[UserRoleSummary] = Field(default_factory=list)
    is_active: bool
    license_active: bool = True
    payg_monthly_budget: Optional[float] = None
    default_billing_rate: Optional[float] = None
    professional_role: Optional[str] = None
    job_title: Optional[str] = None
    office_location: Optional[str] = None
    primary_jurisdictions: List[str] = Field(default_factory=list)
    privacy_mode: bool = False
    workspace_mcp_enabled: bool = True
    created_at: datetime

    model_config = {"from_attributes": True}


class UserList(BaseModel):
    users: List[UserResponse]
    total: int


class UsageStats(BaseModel):
    total_tokens_in: int
    total_tokens_out: int
    total_cost_usd: float
    request_count: int
    period_start: datetime
    period_end: datetime


class BillingUpdate(BaseModel):
    billing_tier: str
    seat_count: Optional[int] = None


class TenantInfo(BaseModel):
    id: str
    name: str
    domain: str
    billing_tier: str
    flat_seat_count: int
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class AuditRecord(BaseModel):
    id: str
    user_id: str
    user_email: Optional[str] = None
    operation_type: Optional[str] = None
    model_used: Optional[str] = None
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    cost_usd: Optional[float] = None
    query_text: Optional[str] = None
    rag_chunks_retrieved: Optional[int] = None
    rag_source_ids: Optional[List[str]] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    created_at: datetime


class AuditLog(BaseModel):
    records: List[AuditRecord]
    total: int
    page: int
    limit: int


class UserUsageRow(BaseModel):
    user_id: str
    user_email: str
    request_count: int
    total_tokens_in: int
    total_tokens_out: int
    total_cost_usd: float


class UserUsageBreakdown(BaseModel):
    users: List[UserUsageRow]
    period_start: datetime
    period_end: datetime


class TenantSettingsResponse(BaseModel):
    """Tenant configuration settings."""

    id: UUID | str
    tenant_id: UUID | str
    # Cache settings
    cache_enabled: bool = True
    cache_ttl_multiplier: float = 1.0
    # User defaults
    default_expertise_level: str = "mid"
    default_practice_areas: List[str] = []
    default_privacy_mode: bool = False
    default_workspace_mcp_enabled: bool = True
    # Feature flags
    enable_auto_memory: bool = True
    enable_pii_detection: bool = True
    enable_skill_routing: bool = True
    enable_matter_context: bool = True
    enable_task_board: bool = True
    # Rate limiting
    max_requests_per_minute: Optional[int] = None
    max_daily_tokens: Optional[int] = None
    # LiteLLM gateway alias overrides (operator-assigned)
    default_llm_provider: Optional[str] = None
    default_llm_model: Optional[str] = None
    premium_llm_provider: Optional[str] = None
    premium_llm_model: Optional[str] = None
    # Cloud storage
    primary_cloud_provider: Optional[str] = None
    # Custom config
    custom_config: Optional[Dict[str, Any]] = None
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @field_serializer("id", "tenant_id", when_used="json")
    def _serialize_uuid(self, value):
        return str(value) if value is not None else None


class TenantSettingsUpdate(BaseModel):
    """Update tenant settings."""

    cache_enabled: Optional[bool] = None
    cache_ttl_multiplier: Optional[float] = None
    default_expertise_level: Optional[str] = None
    default_practice_areas: Optional[List[str]] = None
    default_privacy_mode: Optional[bool] = None
    default_workspace_mcp_enabled: Optional[bool] = None
    enable_auto_memory: Optional[bool] = None
    enable_pii_detection: Optional[bool] = None
    enable_skill_routing: Optional[bool] = None
    enable_matter_context: Optional[bool] = None
    enable_task_board: Optional[bool] = None
    max_requests_per_minute: Optional[int] = None
    max_daily_tokens: Optional[int] = None
    default_llm_provider: Optional[str] = None
    default_llm_model: Optional[str] = None
    premium_llm_provider: Optional[str] = None
    premium_llm_model: Optional[str] = None
    primary_cloud_provider: Optional[str] = None
    custom_config: Optional[Dict[str, Any]] = None
    notes: Optional[str] = None


class TenantDetailResponse(BaseModel):
    """Enhanced tenant information with analytics and settings."""

    id: str
    name: str
    domain: str
    company_name: Optional[str] = None
    staff_size: Optional[int] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    billing_tier: str
    flat_seat_count: int
    is_active: bool
    created_at: datetime
    updated_at: datetime
    # Analytics
    total_users: int = 0
    active_users: int = 0
    total_messages: int = 0
    total_cost_usd: float = 0.0
    cache_hit_rate: Optional[float] = None  # percentage
    avg_response_time_ms: Optional[float] = None


class CacheAnalytics(BaseModel):
    """Cache performance metrics."""

    total_requests: int
    cache_hits: int
    cache_hit_rate: float  # percentage
    rag_hit_rate: float
    llm_hit_rate: float
    matter_hit_rate: float
    avg_hit_latency_ms: Optional[float] = None
    estimated_cost_savings_usd: float


# ── Error Log Schemas ─────────────────────────────────────────────────────


class ErrorLogResponse(BaseModel):
    """Single error log entry."""

    id: str
    tenant_id: str
    user_id: Optional[str] = None
    error_type: str
    severity: str
    message: str
    stack_trace: Optional[str] = None
    endpoint: Optional[str] = None
    method: Optional[str] = None
    status_code: Optional[int] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    query_text: Optional[str] = None
    conversation_id: Optional[str] = None
    is_resolved: bool
    resolved_at: Optional[datetime] = None
    resolution_notes: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class UserErrorLogsResponse(BaseModel):
    """Per-user error logs response."""

    errors: List[ErrorLogResponse]
    total: int
    days: int


class SystemErrorLogsResponse(BaseModel):
    """System-wide error logs response."""

    errors: List[ErrorLogResponse]
    total: int
    days: int
    severity: Optional[str] = None


class ErrorTrendBucket(BaseModel):
    """Daily error count bucket for trend data."""

    date: str  # YYYY-MM-DD
    total: int
    critical: int
    error: int
    warning: int
    info: int


class ErrorSummaryResponse(BaseModel):
    """Error summary with counts by severity/type and daily trend."""

    total_errors: int
    by_severity: Dict[str, int]  # {"critical": N, "error": N, "warning": N, "info": N}
    by_type: Dict[str, int]  # {"api_error": N, "rag_query_error": N, ...}
    trend: List[ErrorTrendBucket]
    days: int


class ErrorResolveRequest(BaseModel):
    """Request to resolve an error log entry."""

    resolution_notes: Optional[str] = None


class ErrorResolveResponse(BaseModel):
    """Response after resolving an error."""

    id: str
    is_resolved: bool
    resolved_at: datetime
    resolution_notes: Optional[str] = None
