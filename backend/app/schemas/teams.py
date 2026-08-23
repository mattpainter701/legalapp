from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class TeamSummary(BaseModel):
    id: str
    display_name: Optional[str] = None


class ChannelSummary(BaseModel):
    id: str
    display_name: Optional[str] = None
    membership_type: Optional[str] = None


class EventTypeSummary(BaseModel):
    """A notification event the admin UI can route to a channel."""

    event_type: str
    label: str
    description: Optional[str] = None


class ChannelCreateRequest(BaseModel):
    team_id: str = Field(min_length=1, max_length=100)
    display_name: str = Field(min_length=1, max_length=50)
    description: Optional[str] = Field(default=None, max_length=1024)

    @field_validator("team_id", "display_name")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class ChannelLinkCreate(BaseModel):
    matter_id: str
    team_id: str = Field(min_length=1, max_length=100)
    channel_id: str = Field(min_length=1, max_length=100)
    team_display_name: Optional[str] = Field(default=None, max_length=255)
    channel_display_name: Optional[str] = Field(default=None, max_length=255)


class ChannelLinkResponse(BaseModel):
    id: UUID
    matter_id: UUID
    # Resolved by the router so the UI can show a matter an admin recognizes
    # instead of a truncated UUID.
    matter_name: Optional[str] = None
    team_id: str
    channel_id: str
    team_display_name: Optional[str] = None
    channel_display_name: Optional[str] = None
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class NotificationSettingItem(BaseModel):
    event_type: str = Field(min_length=1, max_length=100)
    team_id: str = Field(min_length=1, max_length=100)
    channel_id: str = Field(min_length=1, max_length=100)
    team_display_name: Optional[str] = Field(default=None, max_length=255)
    channel_display_name: Optional[str] = Field(default=None, max_length=255)
    matter_id: Optional[str] = None
    is_enabled: bool = True


class NotificationSettingResponse(BaseModel):
    id: UUID
    event_type: str
    team_id: str
    channel_id: str
    team_display_name: Optional[str] = None
    channel_display_name: Optional[str] = None
    matter_id: Optional[UUID] = None
    matter_name: Optional[str] = None
    is_enabled: bool = True

    model_config = {"from_attributes": True}


class NotificationSettingsUpdate(BaseModel):
    # Bounded so a malformed client cannot ask the server to write an unbounded
    # number of routing rows in one transaction.
    settings: list[NotificationSettingItem] = Field(
        default_factory=list, max_length=200
    )


class TestMessageRequest(BaseModel):
    team_id: str = Field(min_length=1, max_length=100)
    channel_id: str = Field(min_length=1, max_length=100)
    matter_name: Optional[str] = Field(default="Test Matter", max_length=255)


# ── Teams voice (Teams Phone capture) ────────────────────────────────────


class VoiceStatusResponse(BaseModel):
    """Everything the admin panel needs to explain the voice setup state."""

    feature_enabled: bool
    configured: bool
    enabled: bool
    entra_tenant_id: Optional[str] = None
    app_credentials_source: Optional[str] = None
    required_application_permission: str
    subscription_active: bool = False
    subscription_expires_at: Optional[datetime] = None
    webhook_url: str
    admin_consent_url: Optional[str] = None
    last_sync_at: Optional[datetime] = None
    last_sync_status: Optional[str] = None
    last_sync_error: Optional[str] = None
    captured_call_count: int = 0
    last_call_at: Optional[datetime] = None


class VoiceSettingsUpdate(BaseModel):
    entra_tenant_id: Optional[str] = Field(default=None, max_length=64)
    is_enabled: Optional[bool] = None


class VoiceTestResponse(BaseModel):
    status: str
    sample_count: int = 0
    inbound_count: int = 0


class VoiceSyncResponse(BaseModel):
    imported: int = 0
    updated: int = 0
    skipped: int = 0
    days: int = 7
