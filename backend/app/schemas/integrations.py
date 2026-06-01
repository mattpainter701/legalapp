from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class IntegrationStatus(BaseModel):
    provider: str
    connected: bool
    scopes: Optional[str] = None
    expires_at: Optional[datetime] = None
    service_account_email: Optional[str] = None


class OAuthRedirectResponse(BaseModel):
    redirect_url: str


class IntegrationsListResponse(BaseModel):
    microsoft: IntegrationStatus
    google: IntegrationStatus
    user_count: int
    tenant_credential_count: int
