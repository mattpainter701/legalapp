from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class IntegrationStatus(BaseModel):
    provider: str
    connected: bool
    scopes: Optional[str] = None
    required_scopes: Optional[str] = None
    missing_scopes: list[str] = []
    expires_at: Optional[datetime] = None
    service_account_email: Optional[str] = None
    last_user_sync_at: Optional[datetime] = None
    last_user_sync_status: Optional[str] = None
    last_user_sync_error: Optional[str] = None
    last_user_sync_total: int = 0
    # Teams (Microsoft only): connected when the granted scopes include all
    # Teams scopes. teams_missing_scopes drives the "Reconnect to enable Teams"
    # prompt in the admin UI.
    teams_connected: bool = False
    teams_missing_scopes: list[str] = []


class OAuthRedirectResponse(BaseModel):
    redirect_url: str


class IntegrationsListResponse(BaseModel):
    microsoft: IntegrationStatus
    google: IntegrationStatus
    user_count: int
    tenant_credential_count: int
