"""Strict contracts for bounded, review-before-approval DOCX revisions."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Union
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class RevisionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
ModelTier = Literal["auto", "standard", "premium"]
RevisionStatus = Literal[
    "processing",
    "needs_input",
    "ready_for_review",
    "approved",
    "rejected",
    "superseded",
    "failed",
]


class MatterDocumentRevisionCreate(RevisionModel):
    instruction: str = Field(min_length=1, max_length=4_000)
    client_request_id: UUID
    model_tier: ModelTier = "auto"


class GeneratedReplaceTextOperation(RevisionModel):
    type: Literal["replace_text"]
    block_id: str = Field(min_length=1, max_length=500)
    target_text: str = Field(min_length=1, max_length=10_000)
    replacement_text: str = Field(max_length=20_000)
    rationale: str | None = Field(default=None, max_length=500)


class GeneratedRevisionChangePlan(RevisionModel):
    outcome: Literal["change_plan"]
    summary: str = Field(min_length=1, max_length=1_000)
    warnings: list[str] = Field(default_factory=list, max_length=10)
    operations: list[GeneratedReplaceTextOperation] = Field(min_length=1, max_length=8)


class GeneratedRevisionNeedsInput(RevisionModel):
    outcome: Literal["needs_input"]
    question: str = Field(min_length=1, max_length=1_000)


GeneratedRevisionResult = Annotated[
    Union[GeneratedRevisionChangePlan, GeneratedRevisionNeedsInput],
    Field(discriminator="outcome"),
]


class RevisionOperationResponse(GeneratedReplaceTextOperation):
    pass


class RevisionTextPreviewBlock(RevisionModel):
    block_id: str
    kind: str
    scope: str
    path: str
    text: str


class SignatureReplacementSignerPreview(RevisionModel):
    signer_id: UUID
    name: str
    email: str
    role: str
    sign_order: int
    status: Literal["pending"]


class SignatureReplacementPreview(RevisionModel):
    eligible: Literal[True] = True
    executable: Literal[False] = False
    semantics: Literal["internal_portal_signature_acknowledgment"] = (
        "internal_portal_signature_acknowledgment"
    )
    notification_will_be_sent: Literal[False] = False
    signature_request_id: UUID
    signature_request_status: Literal["draft", "sent"]
    provider: Literal["internal"] = "internal"
    source_document_id: UUID
    replacement_document_id: UUID
    replacement_document_sha256: Sha256
    signers: list[SignatureReplacementSignerPreview]
    reminders: dict | None = None
    enforce_signing_order: bool = False
    expires_at: datetime | None = None
    prepared_at: datetime
    notice: str


class MatterDocumentRevisionResponse(RevisionModel):
    id: UUID
    matter_id: UUID
    root_document_id: UUID
    source_document_id: UUID
    source_revision_id: UUID | None = None
    output_document_id: UUID | None = None
    client_request_id: UUID
    version_no: int
    instruction: str
    status: RevisionStatus
    clarification_question: str | None = None
    source_filename: str
    source_sha256: Sha256
    output_filename: str | None = None
    output_sha256: Sha256 | None = None
    summary: str | None = None
    warnings: list[str] = Field(default_factory=list)
    operations: list[RevisionOperationResponse] = Field(default_factory=list)
    output_text_preview: list[RevisionTextPreviewBlock] = Field(default_factory=list)
    artifact_url: str | None = None
    requested_model_tier: ModelTier
    resolved_model_tier: str | None = None
    model_alias: str | None = None
    storage_warning: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    approved_by_user_id: UUID | None = None
    approved_at: datetime | None = None
    rejected_by_user_id: UUID | None = None
    rejection_reason: str | None = None
    rejected_at: datetime | None = None
    prepared_esign_preview: SignatureReplacementPreview | None = None
    created_at: datetime
    updated_at: datetime


class MatterDocumentRevisionListResponse(RevisionModel):
    items: list[MatterDocumentRevisionResponse]
    total: int
    limit: int
    offset: int


class MatterDocumentRevisionApprove(RevisionModel):
    reviewed_output_sha256: Sha256


class MatterDocumentRevisionReject(RevisionModel):
    reason: str | None = Field(default=None, max_length=1_000)


class SignatureReplacementPrepare(RevisionModel):
    signature_request_id: UUID
