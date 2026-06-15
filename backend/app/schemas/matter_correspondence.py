"""Pydantic schemas for matter email correspondence capture."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class CorrespondenceRules(BaseModel):
    """Per-matter capture configuration (matter.correspondence_rules)."""

    enabled: bool = False
    match_parties: bool = True
    case_numbers: list[str] = []
    keywords: list[str] = []  # reserved — keyword matching is deferred
    directions: list[str] = ["inbound", "outbound"]  # reserved — v1 captures both


class CorrespondenceScanRequest(BaseModel):
    provider: str  # "microsoft" | "google"
    max_emails: Optional[int] = None


class CorrespondenceScanResponse(BaseModel):
    provider: str
    scanned: int
    captured: int
    skipped: int


class CorrespondenceItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    direction: str
    channel: str
    status: str
    subject: str
    body: Optional[str] = None
    summary: Optional[str] = None
    occurred_at: datetime
    external_ref: Optional[str] = None
    thread_ref: Optional[str] = None
    participants: Optional[dict] = None
    document_id: Optional[uuid.UUID] = None
    has_attachment: bool = False


class CorrespondenceListResponse(BaseModel):
    items: list[CorrespondenceItem]
    total: int
