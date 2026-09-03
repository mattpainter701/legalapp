"""Firm-wide document tags and their per-document assignments.

Tags are tenant-scoped rather than matter-scoped so a firm builds one shared
vocabulary ("Signed", "Privileged", "Needs review") that works across matters;
the link rows are what bind a tag to a single matter document.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

MAX_TAG_NAME_LENGTH = 60
# Tag palette the UI renders; stored as a name, never as raw CSS.
TAG_COLORS = (
    "slate",
    "blue",
    "green",
    "amber",
    "rose",
    "purple",
    "teal",
)
DEFAULT_TAG_COLOR = "slate"


class MatterDocumentTag(Base):
    """A reusable, firm-wide label that can be applied to matter documents."""

    __tablename__ = "matter_document_tags"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_matter_document_tags_tenant_id"),
        CheckConstraint(
            "char_length(btrim(name)) > 0",
            name="ck_matter_document_tags_name_present",
        ),
        CheckConstraint(
            "color IN ('slate', 'blue', 'green', 'amber', 'rose', 'purple', 'teal')",
            name="ck_matter_document_tags_color",
        ),
        Index(
            "uq_matter_document_tags_tenant_name",
            "tenant_id",
            text("lower(name)"),
            unique=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(MAX_TAG_NAME_LENGTH), nullable=False)
    color: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=DEFAULT_TAG_COLOR,
        server_default=DEFAULT_TAG_COLOR,
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
        onupdate=lambda: datetime.now(timezone.utc),
    )


class MatterDocumentTagLink(Base):
    """Assignment of one tag to one matter document."""

    __tablename__ = "matter_document_tag_links"
    __table_args__ = (
        # The composite parents keep a tenant from ever linking across tenants,
        # even if an application-layer check were skipped.
        ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["matter_documents.tenant_id", "matter_documents.id"],
            name="fk_matter_document_tag_links_document",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "tag_id"],
            ["matter_document_tags.tenant_id", "matter_document_tags.id"],
            name="fk_matter_document_tag_links_tag",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "tenant_id",
            "document_id",
            "tag_id",
            name="uq_matter_document_tag_links_assignment",
        ),
        Index(
            "ix_matter_document_tag_links_tenant_tag",
            "tenant_id",
            "tag_id",
        ),
    )

    # A surrogate key rather than the natural (document_id, tag_id) pair: demo
    # fixture cloning remaps rows by a single UUID ``id`` and rejects any clone
    # table without one. Uniqueness of the assignment is kept by the constraint
    # above.
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    tag_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )
