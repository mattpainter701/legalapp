"""Tenant-scoped folder tree used to organize a matter's case documents.

Documents used to render as one flat list per matter. Folders give the UI a
file-explorer tree, and — because every folder carries its materialized
``path`` — they also give the storage layer a route to mirror the same tree
into the firm's bound cloud share (OneDrive / SharePoint / Google Drive).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# Deep trees make the cloud-mirroring path unwieldy and provider path limits
# real; the UI has no affordance past this depth either.
MAX_FOLDER_DEPTH = 8
MAX_FOLDER_NAME_LENGTH = 120

# ``parent_id`` is NULL for top-level folders, and NULL never equals NULL in a
# unique constraint. Fold NULL onto a sentinel so sibling names stay unique at
# the root as well.
ROOT_PARENT_SENTINEL = "00000000-0000-0000-0000-000000000000"


class MatterDocumentFolder(Base):
    """One node of a matter's document folder tree."""

    __tablename__ = "matter_document_folders"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_matter_document_folders_tenant_id"
        ),
        CheckConstraint(
            "depth >= 0 AND depth <= 8",
            name="ck_matter_document_folders_depth",
        ),
        CheckConstraint(
            "char_length(btrim(name)) > 0",
            name="ck_matter_document_folders_name_present",
        ),
        CheckConstraint(
            "position('/' in name) = 0 AND position('\\' in name) = 0",
            name="ck_matter_document_folders_name_no_separator",
        ),
        CheckConstraint(
            "id <> parent_id",
            name="ck_matter_document_folders_not_self_parent",
        ),
        CheckConstraint(
            "kind IN ('user', 'system')",
            name="ck_matter_document_folders_kind",
        ),
        Index(
            "uq_matter_document_folders_sibling_name",
            "tenant_id",
            "matter_id",
            text(f"coalesce(parent_id, '{ROOT_PARENT_SENTINEL}'::uuid)"),
            text("lower(name)"),
            unique=True,
        ),
        Index(
            "ix_matter_document_folders_tenant_matter",
            "tenant_id",
            "matter_id",
        ),
        Index(
            "ix_matter_document_folders_tenant_parent",
            "tenant_id",
            "parent_id",
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
    matter_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("matters.id", ondelete="CASCADE"),
        nullable=False,
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("matter_document_folders.id", ondelete="CASCADE"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(MAX_FOLDER_NAME_LENGTH), nullable=False)
    # Materialized "Discovery/Depositions" path, rebuilt on rename and move. It
    # is what the storage layer replays to mirror the tree into cloud storage.
    path: Mapped[str] = mapped_column(String(1200), nullable=False)
    depth: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # 'system' folders are created by the product (e.g. client portal uploads)
    # and are protected from rename and delete so their contract stays stable.
    kind: Mapped[str] = mapped_column(
        String(20), nullable=False, default="user", server_default="user"
    )
    system_key: Mapped[str | None] = mapped_column(String(60), nullable=True)
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

    @property
    def path_segments(self) -> list[str]:
        """Folder names from the matter root down to this folder."""
        return [segment for segment in (self.path or "").split("/") if segment]
