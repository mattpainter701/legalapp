"""Provider-neutral external import staging models."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ExternalSystemConnection(Base):
    """A configured source system used for migration/import work."""

    __tablename__ = "external_system_connections"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "provider",
            "external_key",
            name="uq_external_system_connections_source",
        ),
        Index("idx_external_system_connections_tenant", "tenant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    external_key: Mapped[str] = mapped_column(String(200), nullable=False)
    display_name: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(
        String(50), default="active", server_default="active", nullable=False
    )
    accounting_mode: Mapped[str] = mapped_column(
        String(50), default="tabs3_reference", server_default="tabs3_reference"
    )
    source_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    last_import_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    last_import_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
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


class ExternalImportRun(Base):
    """One uploaded export bundle and its staging/promote lifecycle."""

    __tablename__ = "external_import_runs"
    __table_args__ = (
        Index("idx_external_import_runs_tenant", "tenant_id"),
        Index("idx_external_import_runs_connection", "connection_id"),
        Index("idx_external_import_runs_status", "tenant_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("external_system_connections.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    source_system: Mapped[str | None] = mapped_column(String(200), nullable=True)
    export_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(
        String(50), default="uploaded", server_default="uploaded", nullable=False
    )
    manifest: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    row_counts: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    checksum_summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    warnings: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    errors: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    promoted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
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


class ExternalRawRow(Base):
    """Immutable raw source row staged from an import bundle."""

    __tablename__ = "external_raw_rows"
    __table_args__ = (
        UniqueConstraint(
            "import_run_id",
            "source_table",
            "source_row_key",
            name="uq_external_raw_rows_run_table_key",
        ),
        Index("idx_external_raw_rows_tenant", "tenant_id"),
        Index("idx_external_raw_rows_table", "tenant_id", "provider", "source_table"),
        Index("idx_external_raw_rows_run", "import_run_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    import_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("external_import_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    source_table: Mapped[str] = mapped_column(String(100), nullable=False)
    source_row_key: Mapped[str] = mapped_column(String(300), nullable=False)
    row_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    row_data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    source_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )


class ExternalRecordLink(Base):
    """Idempotency/provenance link from a source row to a WellPled record."""

    __tablename__ = "external_record_links"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "provider",
            "source_table",
            "source_row_key",
            "target_table",
            name="uq_external_record_links_source_target",
        ),
        Index("idx_external_record_links_tenant", "tenant_id"),
        Index(
            "idx_external_record_links_source", "tenant_id", "provider", "source_table"
        ),
        Index(
            "idx_external_record_links_target",
            "tenant_id",
            "target_table",
            "target_record_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    source_table: Mapped[str] = mapped_column(String(100), nullable=False)
    source_row_key: Mapped[str] = mapped_column(String(300), nullable=False)
    import_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("external_import_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    target_table: Mapped[str] = mapped_column(String(100), nullable=False)
    target_record_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(50), default="linked", server_default="linked", nullable=False
    )
    confidence: Mapped[str | None] = mapped_column(String(50), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
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
