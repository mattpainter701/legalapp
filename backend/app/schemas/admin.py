from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel
from uuid import UUID


class UserResponse(BaseModel):
    id: str
    email: str
    full_name: Optional[str] = None
    role: str
    is_active: bool
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
