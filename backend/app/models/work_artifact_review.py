"""Revision-bound review requirements and immutable approval/delivery evidence."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ArtifactEvidence:
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    artifact_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    revision_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


def revision_fk():
    return ForeignKeyConstraint(
        ["tenant_id", "artifact_id", "revision_id"],
        [
            "generated_artifact_revisions.tenant_id",
            "generated_artifact_revisions.artifact_id",
            "generated_artifact_revisions.id",
        ],
        ondelete="RESTRICT",
    )


class WorkArtifactReviewRequirement(ArtifactEvidence, Base):
    __tablename__ = "work_artifact_review_requirement"
    __table_args__ = (
        revision_fk(),
        UniqueConstraint(
            "tenant_id",
            "artifact_id",
            "revision_id",
            "id",
            name="uq_artifact_requirement_binding",
        ),
        UniqueConstraint(
            "tenant_id",
            "artifact_id",
            "revision_id",
            "review_round",
            "sequence",
            name="uq_artifact_requirement_round",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "reviewer_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "sequence > 0 AND review_round > 0", name="ck_artifact_requirement_sequence"
        ),
        CheckConstraint(
            "reviewer_role IN ('staff', 'attorney')",
            name="ck_artifact_requirement_role",
        ),
        CheckConstraint(
            "status IN ('pending', 'approved', 'changes_requested', 'skipped', 'superseded')",
            name="ck_artifact_requirement_status",
        ),
        CheckConstraint(
            "(status = 'superseded') = (superseded_at IS NOT NULL)",
            name="ck_artifact_requirement_superseded",
        ),
        Index(
            "uq_artifact_requirement_current",
            "tenant_id",
            "artifact_id",
            "revision_id",
            "reviewer_role",
            unique=True,
            postgresql_where=text("superseded_at IS NULL"),
        ),
    )
    review_round: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    reviewer_role: Mapped[str] = mapped_column(String(20), nullable=False)
    reviewer_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkArtifactApproval(ArtifactEvidence, Base):
    __tablename__ = "work_artifact_approval"
    __table_args__ = (
        revision_fk(),
        UniqueConstraint(
            "tenant_id",
            "artifact_id",
            "revision_id",
            "id",
            name="uq_artifact_approval_binding",
        ),
        UniqueConstraint("requirement_id", name="uq_artifact_approval_requirement"),
        ForeignKeyConstraint(
            ["tenant_id", "artifact_id", "revision_id", "requirement_id"],
            [
                "work_artifact_review_requirement.tenant_id",
                "work_artifact_review_requirement.artifact_id",
                "work_artifact_review_requirement.revision_id",
                "work_artifact_review_requirement.id",
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "reviewer_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["matter_documents.tenant_id", "matter_documents.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "decision IN ('approve', 'request_changes', 'override')",
            name="ck_artifact_approval_decision",
        ),
        CheckConstraint(
            "decision <> 'override' OR (reason IS NOT NULL AND length(btrim(reason)) > 0)",
            name="ck_artifact_approval_override_reason",
        ),
        CheckConstraint(
            "content_sha256 ~ '^[a-f0-9]{64}$' AND document_sha256 ~ '^[a-f0-9]{64}$'",
            name="ck_artifact_approval_hashes",
        ),
    )
    requirement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    reviewer_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    decision: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    document_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    purpose: Mapped[str] = mapped_column(
        String(40), nullable=False, default="approve_document"
    )
    stamp_metadata: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class WorkArtifactDelivery(ArtifactEvidence, Base):
    """Append one receipt per attempt/state; never overwrite provider uncertainty."""

    __tablename__ = "work_artifact_delivery"
    __table_args__ = (
        revision_fk(),
        ForeignKeyConstraint(
            ["tenant_id", "artifact_id", "revision_id", "approval_id"],
            [
                "work_artifact_approval.tenant_id",
                "work_artifact_approval.artifact_id",
                "work_artifact_approval.revision_id",
                "work_artifact_approval.id",
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "actor_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "tenant_id",
            "attempt_id",
            "status",
            name="uq_artifact_delivery_attempt_state",
        ),
        CheckConstraint(
            "status IN ('queued', 'submitted', 'sent', 'failed', 'outcome_unknown')",
            name="ck_artifact_delivery_status",
        ),
        CheckConstraint(
            "channel IN ('email', 'sms', 'filing')", name="ck_artifact_delivery_channel"
        ),
        CheckConstraint(
            "document_sha256 ~ '^[a-f0-9]{64}$'", name="ck_artifact_delivery_hash"
        ),
        Index(
            "ix_artifact_delivery_timeline", "tenant_id", "artifact_id", "created_at"
        ),
    )
    approval_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    actor_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    attempt_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    document_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    recipient_bindings: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    provider_message_id: Mapped[str | None] = mapped_column(String(500))
    detail: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
