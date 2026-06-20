from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    full_name: Optional[str] = None
    company_name: Optional[str] = None
    staff_size: Optional[int] = None
    address: Optional[str] = None
    phone: Optional[str] = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    password: str = Field(min_length=12, max_length=128)


class OAuthCallbackExchangeRequest(BaseModel):
    code: str = Field(min_length=16, max_length=256)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    tenant_id: str
    role: str
    email: str
    full_name: Optional[str] = None


class UserInfo(BaseModel):
    id: str
    tenant_id: str
    email: str
    full_name: Optional[str] = None
    role: str
    is_active: bool
    license_active: bool = True
    premium_ai_enabled: bool = False
    created_at: datetime
    billing_tier: str
    enabled_modules: list[str] = []
    default_route: str = "/matters"

    model_config = {"from_attributes": True}
