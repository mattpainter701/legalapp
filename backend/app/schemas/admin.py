from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel


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
