"""Durable intake requirements and delivery state for one matter."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MatterIntake(Base):
    __tablename__ = "matter_intakes"
    __table_args__ = (
        UniqueConstraint("tenant_id", "matter_id", name="uq_matter_intakes_matter"),
        ForeignKeyConstraint(
            ["tenant_id", "matter_id"],
            ["matters.tenant_id", "matters.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "contact_id"], ["contacts.tenant_id", "contacts.id"]
        ),
        ForeignKeyConstraint(
            ["tenant_id", "owner_id"], ["users.tenant_id", "users.id"]
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by"], ["users.tenant_id", "users.id"]
        ),
        ForeignKeyConstraint(
            ["tenant_id", "signature_id"],
            ["signature_requests.tenant_id", "signature_requests.id"],
        ),
        ForeignKeyConstraint(
            ["tenant_id", "invite_id"],
            ["client_portal_invites.tenant_id", "client_portal_invites.id"],
        ),
        Index("ix_matter_intakes_tenant_status", "tenant_id", "status"),
    )
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE")
    )
    matter_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    contact_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    signature_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("signature_requests.id"), nullable=False
    )
    invite_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("client_portal_invites.id"), nullable=False
    )
    encrypted_invite: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="awaiting_documents"
    )
    config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    requirements: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    answers: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    delivery: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    meeting: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
