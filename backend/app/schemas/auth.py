from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr, Field, field_validator

from app.schemas.demo import DemoInfo

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
    staff_size: Optional[int] = Field(default=None, ge=1)
    address: Optional[str] = Field(default=None, max_length=500)
    phone: Optional[str] = Field(default=None, max_length=50)

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
    professional_role: Optional[str] = None
    job_title: Optional[str] = None
    office_location: Optional[str] = None
    primary_jurisdictions: list[str] = Field(default_factory=list)
    privacy_mode: bool = False
    demo: Optional[DemoInfo] = None

    model_config = {"from_attributes": True}


class UserProfileUpdate(BaseModel):
    """Fields a user may manage for their verified professional profile."""

    professional_role: Optional[str] = Field(default=None, max_length=120)
    job_title: Optional[str] = Field(default=None, max_length=160)
    office_location: Optional[str] = Field(default=None, max_length=255)
    primary_jurisdictions: Optional[list[str]] = Field(default=None, max_length=25)
    privacy_mode: Optional[bool] = None

    model_config = {"extra": "forbid"}

    @field_validator("professional_role", "job_title", "office_location")
    @classmethod
    def normalize_optional_text(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("primary_jurisdictions")
    @classmethod
    def normalize_jurisdictions(cls, value: Optional[list[str]]) -> list[str]:
        if value is None:
            # The persisted JSON column is non-null; explicit null means clear.
            return []
        cleaned = []
        for jurisdiction in value:
            normalized = jurisdiction.strip()
            if not normalized or len(normalized) > 100:
                raise ValueError(
                    "Each jurisdiction must be between 1 and 100 characters"
                )
            if normalized not in cleaned:
                cleaned.append(normalized)
        return cleaned
