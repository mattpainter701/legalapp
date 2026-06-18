from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class TeamSummary(BaseModel):
    id: str
    display_name: Optional[str] = None


class ChannelSummary(BaseModel):
    id: str
    display_name: Optional[str] = None
    membership_type: Optional[str] = None


class ChannelCreateRequest(BaseModel):
    team_id: str
    display_name: str
    description: Optional[str] = None


class ChannelLinkCreate(BaseModel):
    matter_id: str
    team_id: str
    channel_id: str
    team_display_name: Optional[str] = None
    channel_display_name: Optional[str] = None


class ChannelLinkResponse(BaseModel):
    id: UUID
    matter_id: UUID
    team_id: str
    channel_id: str
    team_display_name: Optional[str] = None
    channel_display_name: Optional[str] = None
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class NotificationSettingItem(BaseModel):
    event_type: str
    team_id: str
    channel_id: str
    team_display_name: Optional[str] = None
    channel_display_name: Optional[str] = None
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
    is_enabled: bool = True

    model_config = {"from_attributes": True}


class NotificationSettingsUpdate(BaseModel):
    settings: list[NotificationSettingItem]


class TestMessageRequest(BaseModel):
    team_id: str
    channel_id: str
    matter_name: Optional[str] = "Test Matter"

