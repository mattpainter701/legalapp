"""Firm Memory source catalog and authorization metadata.

Firm Memory documents remain owned by their native systems.  These tables
describe searchable sources and policy; they intentionally do not create a
second cloud corpus for on-prem content.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class FirmMemorySource(Base):
    __tablename__ = "firm_memory_sources"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_firm_memory_sources_tenant_id"),
        UniqueConstraint("tenant_id", "source_key", name="uq_firm_memory_sources_key"),
        ForeignKeyConstraint(
            ["tenant_id", "legacy_smb_share_id"],
            ["smb_shares.tenant_id", "smb_shares.id"],
            name="fk_firm_memory_sources_legacy_smb_share",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "source_kind IN ('smb', 'cloud', 'matter_documents', 'native')",
            name="ck_firm_memory_sources_kind",
        ),
        CheckConstraint(
            "authorization_mode IN ('firm', 'matter', 'explicit', 'native')",
            name="ck_firm_memory_sources_authorization_mode",
        ),
        CheckConstraint(
            "coverage_state IN ('ready', 'partial', 'indexing', 'stale', 'offline', 'unsupported')",
            name="ck_firm_memory_sources_coverage_state",
        ),
        CheckConstraint(
            "source_kind = 'smb' OR legacy_smb_share_id IS NULL",
            name="ck_firm_memory_sources_legacy_share_kind",
        ),
        CheckConstraint(
            "authorization_mode <> 'native' OR native_authorizer_key IS NOT NULL",
            name="ck_firm_memory_sources_native_authorizer",
        ),
        Index("ix_firm_memory_sources_tenant_kind", "tenant_id", "source_kind"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    source_key: Mapped[str] = mapped_column(String(120), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    provider_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    authorization_mode: Mapped[str] = mapped_column(
        String(20), nullable=False, default="explicit", server_default="explicit"
    )
    native_authorizer_key: Mapped[str | None] = mapped_column(
        String(120), nullable=True
    )
    legacy_smb_share_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    coverage_state: Mapped[str] = mapped_column(
        String(20), nullable=False, default="unsupported", server_default="unsupported"
    )
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
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


class FirmMemoryCollection(Base):
    __tablename__ = "firm_memory_collections"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_firm_memory_collections_tenant_id"
        ),
        UniqueConstraint(
            "tenant_id", "collection_key", name="uq_firm_memory_collections_key"
        ),
        Index("ix_firm_memory_collections_tenant", "tenant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    collection_key: Mapped[str] = mapped_column(String(120), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )


class FirmMemoryCollectionSource(Base):
    __tablename__ = "firm_memory_collection_sources"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "collection_id",
            "source_id",
            name="uq_firm_memory_collection_source",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "collection_id"],
            ["firm_memory_collections.tenant_id", "firm_memory_collections.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["firm_memory_sources.tenant_id", "firm_memory_sources.id"],
            ondelete="CASCADE",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    collection_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)


class FirmMemorySourceGrant(Base):
    __tablename__ = "firm_memory_source_grants"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "source_id",
            "subject_type",
            "subject_id",
            name="uq_firm_memory_source_grant_subject",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["firm_memory_sources.tenant_id", "firm_memory_sources.id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "subject_type IN ('user', 'role')",
            name="ck_firm_memory_source_grants_subject",
        ),
        CheckConstraint(
            "effect IN ('allow', 'deny')", name="ck_firm_memory_source_grants_effect"
        ),
        Index(
            "ix_firm_memory_source_grants_subject",
            "tenant_id",
            "subject_type",
            "subject_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(20), nullable=False)
    subject_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    effect: Mapped[str] = mapped_column(
        String(10), nullable=False, default="deny", server_default="deny"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )


class FirmMemoryMatterPolicy(Base):
    __tablename__ = "firm_memory_matter_policies"
    __table_args__ = (
        UniqueConstraint("tenant_id", "matter_id", name="uq_firm_memory_matter_policy"),
        ForeignKeyConstraint(
            ["tenant_id", "matter_id"],
            ["matters.tenant_id", "matters.id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "access_mode IN ('firm', 'assigned', 'restricted')",
            name="ck_firm_memory_matter_policies_mode",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    matter_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    access_mode: Mapped[str] = mapped_column(
        String(20), nullable=False, default="restricted", server_default="restricted"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )


class FirmMemoryMatterGrant(Base):
    __tablename__ = "firm_memory_matter_grants"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "matter_id", "user_id", name="uq_firm_memory_matter_grant"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "matter_id"],
            ["matters.tenant_id", "matters.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="CASCADE",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    matter_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)


class FirmMemoryDocumentMatter(Base):
    __tablename__ = "firm_memory_document_matters"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "source_id",
            "document_key",
            "matter_id",
            name="uq_firm_memory_document_matter",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["firm_memory_sources.tenant_id", "firm_memory_sources.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "matter_id"],
            ["matters.tenant_id", "matters.id"],
            ondelete="CASCADE",
        ),
        Index(
            "ix_firm_memory_document_matters_document",
            "tenant_id",
            "source_id",
            "document_key",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    document_key: Mapped[str] = mapped_column(String(500), nullable=False)
    matter_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)


class FirmMemoryDocumentWorkspace(Base):
    __tablename__ = "firm_memory_document_workspaces"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "source_id",
            "document_key",
            "workspace_id",
            name="uq_firm_memory_document_workspace",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["firm_memory_sources.tenant_id", "firm_memory_sources.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["research_workspaces.tenant_id", "research_workspaces.id"],
            ondelete="CASCADE",
        ),
        Index(
            "ix_firm_memory_document_workspaces_document",
            "tenant_id",
            "source_id",
            "document_key",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    document_key: Mapped[str] = mapped_column(String(500), nullable=False)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
