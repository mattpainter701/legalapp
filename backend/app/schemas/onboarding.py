"""Schemas for tenant onboarding wizard."""

from pydantic import BaseModel


class IntegrationConnectionStatus(BaseModel):
    connected: bool
    scopes: str | None = None
    service_account_email: str | None = None
    granted_by_user_id: str | None = None


class OnboardingStatusResponse(BaseModel):
    onboarding_completed: bool
    onboarding_step: int
    integrations: dict[str, IntegrationConnectionStatus]  # "microsoft", "google"
    synced_users: dict[str, int]  # "microsoft": N, "google": N
    total_users: int


class OnboardingCompleteResponse(BaseModel):
    status: str
    cloud_root: dict | None = None


class OnboardingStepUpdate(BaseModel):
    step: int
