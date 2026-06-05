"""M:N assignment model linking users to matters with roles."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class MatterAssignment(Base):
    """Which users are assigned to each matter, with role and primary flag."""

    __tablename__ = "matter_assignments"
    __table_args__ = (
        UniqueConstraint("matter_id", "user_id", name="uq_matter_assignment"),
        Index("idx_matter_assignments_user", "tenant_id", "user_id"),
        Index("idx_matter_assignments_matter", "matter_id"),
    )

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
    matter_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("matters.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(
        String(50), nullable=False, default="associate", server_default="associate"
    )
    is_primary: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    is_active_working: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
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

    # Relationships
    matter: Mapped["Matter"] = relationship("Matter", back_populates="assignments")
    user: Mapped["User"] = relationship("User", lazy="joined")
