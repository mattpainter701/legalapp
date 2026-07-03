from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr, Field, field_validator

from app.utils.password_policy import is_common_password


def _reject_common_password(value: str) -> str:
    if is_common_password(value):
        raise ValueError(
            "This password is too common. Please choose a more unique password."
        )
    return value


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    full_name: Optional[str] = None
    company_name: Optional[str] = None
    staff_size: Optional[int] = None
    address: Optional[str] = None
    phone: Optional[str] = None

    _validate_password = field_validator("password")(_reject_common_password)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class PlanSignupRequest(BaseModel):
    plan: str
    firm_name: str = Field(min_length=1, max_length=255)
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    full_name: Optional[str] = None

    _validate_password = field_validator("password")(_reject_common_password)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    password: str = Field(min_length=12, max_length=128)

    _validate_password = field_validator("password")(_reject_common_password)


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
    plan: str = "full-platform"
    upsell_target: Optional[str] = None

    model_config = {"from_attributes": True}
