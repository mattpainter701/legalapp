"""Tenant and matter-scoped collaborative research workspace records."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ResearchWorkspace(Base):
    __tablename__ = "research_workspaces"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_research_workspaces_tenant_id"),
        CheckConstraint(
            "btrim(title) <> ''", name="ck_research_workspaces_title_not_blank"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "matter_id"],
            ["matters.tenant_id", "matters.id"],
            ondelete="RESTRICT",
        ),
        Index(
            "ix_research_workspaces_tenant_matter_created",
            "tenant_id",
            "matter_id",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    matter_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
        onupdate=lambda: datetime.now(timezone.utc),
    )


class ResearchWorkspaceMember(Base):
    __tablename__ = "research_workspace_members"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "user_id", name="uq_research_workspace_member"
        ),
        Index("ix_research_workspace_members_user", "tenant_id", "user_id"),
        CheckConstraint(
            "role IN ('owner', 'editor', 'reviewer', 'viewer')",
            name="ck_research_workspace_members_role",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["research_workspaces.tenant_id", "research_workspaces.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )


class ResearchRecord(Base):
    """A workspace item.  Its structured payload keeps the source/evidence contract intact."""

    __tablename__ = "research_records"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "id",
            name="uq_research_records_tenant_workspace_id",
        ),
        Index(
            "ix_research_records_workspace_order",
            "tenant_id",
            "workspace_id",
            "record_type",
            "sort_order",
        ),
        CheckConstraint(
            "record_type IN ('issue', 'search', 'folder', 'authority', 'highlight', 'annotation', 'exclusion', 'outline', 'memo', 'alert')",
            name="ck_research_records_type",
        ),
        CheckConstraint(
            "evidence_class IN ('cited', 'verify', 'model')",
            name="ck_research_records_evidence_class",
        ),
        CheckConstraint(
            "currentness_state IN ('unknown', 'current', 'stale', 'review_needed', 'unavailable')",
            name="ck_research_records_currentness",
        ),
        CheckConstraint(
            "treatment_state IN ('unknown', 'favorable', 'negative', 'neutral', 'caution', 'review_needed', 'unavailable')",
            name="ck_research_records_treatment",
        ),
        CheckConstraint("sort_order >= 0", name="ck_research_records_sort_order"),
        CheckConstraint("revision > 0", name="ck_research_records_revision"),
        CheckConstraint(
            "(evidence_class <> 'cited' OR source_url IS NOT NULL AND btrim(source_url) <> '')",
            name="ck_research_records_cited_source",
        ),
        CheckConstraint(
            "(record_type <> 'exclusion' OR exclusion_reason IS NOT NULL AND btrim(exclusion_reason) <> '')",
            name="ck_research_records_exclusion_reason",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["research_workspaces.tenant_id", "research_workspaces.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "folder_id"],
            [
                "research_records.tenant_id",
                "research_records.workspace_id",
                "research_records.id",
            ],
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    record_type: Mapped[str] = mapped_column(String(30), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    body: Mapped[str | None] = mapped_column(Text())
    evidence_class: Mapped[str] = mapped_column(
        String(12), nullable=False, default="model", server_default="model"
    )
    source_url: Mapped[str | None] = mapped_column(Text())
    source_version: Mapped[str | None] = mapped_column(String(200))
    source_as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    currentness_state: Mapped[str] = mapped_column(
        String(30), nullable=False, default="unknown", server_default="unknown"
    )
    treatment_state: Mapped[str] = mapped_column(
        String(40), nullable=False, default="unknown", server_default="unknown"
    )
    pinpoint: Mapped[str | None] = mapped_column(String(300))
    quote: Mapped[str | None] = mapped_column(Text())
    exclusion_reason: Mapped[str | None] = mapped_column(Text())
    assigned_reviewer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    folder_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
        onupdate=lambda: datetime.now(timezone.utc),
    )


class ResearchWorkspaceEvent(Base):
    __tablename__ = "research_workspace_events"
    __table_args__ = (
        Index(
            "ix_research_workspace_events_workspace_created",
            "tenant_id",
            "workspace_id",
            "created_at",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["research_workspaces.tenant_id", "research_workspaces.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "record_id"],
            [
                "research_records.tenant_id",
                "research_records.workspace_id",
                "research_records.id",
            ],
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    record_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    detail: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )


class ResearchWorkspaceSnapshot(Base):
    __tablename__ = "research_workspace_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "sequence", name="uq_research_workspace_snapshot_sequence"
        ),
        Index(
            "ix_research_workspace_snapshots_workspace_created",
            "tenant_id",
            "workspace_id",
            "created_at",
        ),
        CheckConstraint(
            "sequence > 0", name="ck_research_workspace_snapshots_sequence"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["research_workspaces.tenant_id", "research_workspaces.id"],
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )


class ResearchWorkspaceIdempotency(Base):
    __tablename__ = "research_workspace_idempotency"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "actor_user_id",
            "operation",
            "idempotency_key",
            name="uq_research_workspace_idempotency",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "actor_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    actor_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    operation: Mapped[str] = mapped_column(String(40), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    response_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )


class ResearchRecordRevision(Base):
    __tablename__ = "research_record_revisions"
    __table_args__ = (
        UniqueConstraint("record_id", "revision", name="uq_research_record_revision"),
        CheckConstraint("revision > 0", name="ck_research_record_revisions_revision"),
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "record_id"],
            [
                "research_records.tenant_id",
                "research_records.workspace_id",
                "research_records.id",
            ],
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    record_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )
