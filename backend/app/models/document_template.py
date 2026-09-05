"""DocumentTemplate model — reusable templates with variable substitution."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class DocumentTemplate(Base):
    __tablename__ = "document_templates"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(
        String(50), default="other", server_default="other"
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    visibility: Mapped[str | None] = mapped_column(
        String(50), default="tenant", server_default="tenant"
    )
    layer: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str | None] = mapped_column(
        String(50), default="draft", server_default="draft"
    )
    format: Mapped[str | None] = mapped_column(
        String(50), default="markdown", server_default="markdown"
    )
    module: Mapped[str | None] = mapped_column(String(100), nullable=True)
    stage: Mapped[str | None] = mapped_column(String(200), nullable=True)
    jurisdiction: Mapped[str | None] = mapped_column(String(300), nullable=True)
    kind: Mapped[str | None] = mapped_column(String(100), nullable=True)
    variable_schema: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    signer_roles: Mapped[list | None] = mapped_column(JSON, nullable=True)
    branding_profile: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    source_storage_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    source_filename: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_file_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_test_rendered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )
    #: Immutable version whose content exactly matches this authoring row.
    #: Zero means the template predates versioning and has not been edited.
    current_version_no: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    tested_version_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    published_version_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
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
