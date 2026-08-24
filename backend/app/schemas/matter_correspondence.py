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
    # Read-only: generated from this matter's client and party contacts. This is
    # surfaced beside the rules so users can see exactly what party-email
    # matching will use.
    tracked_addresses: list[str] = []


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


class MatterInboundAlias(BaseModel):
    id: uuid.UUID
    address: str
    status: str
    last_received_at: Optional[datetime] = None
    created_at: datetime


class MatterInboundAliasResponse(BaseModel):
    enabled: bool
    domain: str
    alias: Optional[MatterInboundAlias] = None


class InboundEmailItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: str
    envelope_sender: str
    recipient: str
    subject: str
    body_preview: Optional[str] = None
    participants: Optional[dict] = None
    authentication_results: Optional[dict] = None
    provider_message_id: Optional[str] = None
    raw_size: int
    occurred_at: datetime
    reviewed_at: Optional[datetime] = None
    communication_log_id: Optional[uuid.UUID] = None
    created_at: datetime


class InboundEmailListResponse(BaseModel):
    items: list[InboundEmailItem]
    total: int


class InboundEmailReviewResponse(BaseModel):
    id: uuid.UUID
    status: str
    communication_log_id: Optional[uuid.UUID] = None
