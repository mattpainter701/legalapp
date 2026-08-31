from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class FileOpenIntentCreate(BaseModel):
    file_id: str = Field(..., min_length=1, max_length=64)
    matter_id: str | None = Field(None, max_length=64)
    action: Literal["open", "show"] = "open"


class FileOpenIntentCreated(BaseModel):
    launch_url: str
    expires_at: datetime
    file_id: str
    agent_id: str
    share_id: str
    source_id: str | None = None
    file_revision: str | None = None
    action: str


class FileOpenIntentRedeemRequest(BaseModel):
    handle: str = Field(..., min_length=20, max_length=200)
    action: Literal["open", "show"]
    session_id: str = Field(..., min_length=1, max_length=32, pattern=r"^[0-9]+$")
    user_sid: str = Field(..., min_length=5, max_length=200, pattern=r"^S-[0-9-]+$")


class FileOpenIntentRedeemed(BaseModel):
    intent_id: str
    file_id: str
    source_id: str
    file_revision: str
    agent_id: str
    share_id: str
    matter_id: str | None = None
    action: str
    nonce: str


class FileOpenIntentOutcomeRequest(BaseModel):
    outcome: Literal[
        "opened",
        "shown",
        "access_denied",
        "moved",
        "offline",
        "unreachable",
        "expired",
        "failed",
    ]


class FileOpenIntentOutcomeResponse(BaseModel):
    status: str
