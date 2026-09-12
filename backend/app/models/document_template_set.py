"""A set: the templates a firm drafts together.

A filing is rarely one document. Drafting each template on its own means
answering the same caption once per document, which is the exact tedium
document automation exists to remove.

A set is an ordered group whose members are drafted from one interview. Two
properties are load-bearing:

* **A member may pin a version.** ``pinned_version_no`` NULL means "whatever is
  published now", which is what a firm wants for a letterhead that keeps
  improving. An integer pins one immutable version, which is what a firm needs
  to reproduce a packet it filed two years ago. Neither is right for every
  member, so the choice is per item.
* **Membership is not a copy.** The set references templates; it never holds
  their content. A template edited after being added to a set is still the same
  template, and the set's next draft uses whatever that template's lifecycle
  says is publishable — the version gate is not routed around here.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

#: Ceiling on members. A set is a packet a person reviews before it leaves the
#: firm; past this the review stops being real.
MAX_SET_MEMBERS = 20


class DocumentTemplateSet(Base):
    __tablename__ = "document_template_sets"
    __table_args__ = (
        UniqueConstraint("tenant_id", "title", name="uq_document_template_sets_title"),
        Index("ix_document_template_sets_tenant", "tenant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    module: Mapped[str | None] = mapped_column(String(50), nullable=True)
    jurisdiction: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    items = relationship(
        "DocumentTemplateSetItem",
        back_populates="template_set",
        cascade="all, delete-orphan",
        order_by="DocumentTemplateSetItem.position",
        lazy="selectin",
    )


class DocumentTemplateSetItem(Base):
    __tablename__ = "document_template_set_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "template_id"],
            ["document_templates.tenant_id", "document_templates.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "set_id", "template_id", name="uq_document_template_set_items_template"
        ),
        UniqueConstraint(
            "set_id", "position", name="uq_document_template_set_items_position"
        ),
        CheckConstraint(
            "position >= 0", name="ck_document_template_set_items_position"
        ),
        CheckConstraint(
            "pinned_version_no IS NULL OR pinned_version_no > 0",
            name="ck_document_template_set_items_version",
        ),
        Index("ix_document_template_set_items_set", "tenant_id", "set_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    set_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document_template_sets.id", ondelete="CASCADE"),
        nullable=False,
    )
    template_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    #: NULL follows the template's published version; an integer reproduces one
    #: exact immutable version, so a filed packet stays reproducible.
    pinned_version_no: Mapped[int | None] = mapped_column(Integer, nullable=True)

    template_set = relationship("DocumentTemplateSet", back_populates="items")
