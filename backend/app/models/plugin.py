"""SQLAlchemy models for the legal practice plugin system."""

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class PracticeProfile(Base):
    """Per-tenant, per-plugin practice profile (the CLAUDE.md equivalent)."""

    __tablename__ = "practice_profiles"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "plugin_name", name="uq_practice_profiles_tenant_plugin"
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
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    plugin_name: Mapped[str] = mapped_column(String(100), nullable=False)
    profile_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_complete: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    setup_step: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
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


class Matter(Base):
    """Litigation portfolio ledger — one row per matter."""

    __tablename__ = "matters"
    __table_args__ = (
        UniqueConstraint("tenant_id", "slug", name="uq_matters_tenant_slug"),
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
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    slug: Mapped[str] = mapped_column(String(200), nullable=False)
    matter_name: Mapped[str] = mapped_column(String(500), nullable=False)
    matter_type: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[str] = mapped_column(String(100), nullable=False)
    counterparty: Mapped[str] = mapped_column(String(500), nullable=False)
    jurisdiction: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(
        String(100), default="threatened", server_default="threatened"
    )
    stage: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source: Mapped[str | None] = mapped_column(String(500), nullable=True)
    risk_level: Mapped[str | None] = mapped_column(String(50), nullable=True)
    materiality: Mapped[str | None] = mapped_column(String(50), nullable=True)
    exposure_range: Mapped[str | None] = mapped_column(String(200), nullable=True)
    outside_counsel: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    internal_owners: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    conflicts_status: Mapped[str] = mapped_column(
        String(50), default="not-run", server_default="not-run"
    )
    conflicts_override_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    legal_hold_issued: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    legal_hold_details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    key_dates: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    initial_posture: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision: Mapped[str | None] = mapped_column(String(50), nullable=True)
    is_closed: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    outcome: Mapped[str | None] = mapped_column(String(200), nullable=True)
    final_cost: Mapped[str | None] = mapped_column(String(100), nullable=True)
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


class MatterEvent(Base):
    """Append-only event log for a matter."""

    __tablename__ = "matter_events"

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
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )


class Renewal(Base):
    """Contract renewal register."""

    __tablename__ = "renewals"

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
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    contract_name: Mapped[str] = mapped_column(String(500), nullable=False)
    vendor: Mapped[str] = mapped_column(String(300), nullable=False)
    contract_value_annual: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    renewal_date: Mapped[datetime] = mapped_column(Date, nullable=False)
    notice_deadline: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    auto_renewal: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )
    price_increase_pct: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2), nullable=True
    )
    business_owner: Mapped[str | None] = mapped_column(String(300), nullable=True)
    business_owner_email: Mapped[str | None] = mapped_column(String(300), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(50), default="pending", server_default="pending"
    )
    decision_deadline: Mapped[datetime | None] = mapped_column(Date, nullable=True)
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


class Estate(Base):
    """Trust & Estate matter — one row per estate administration."""

    __tablename__ = "estates"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    grantor: Mapped[str | None] = mapped_column(String(300), nullable=True)
    estate_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(
        String(100), default="active", server_default="active"
    )
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    events: Mapped[list["EstateEvent"]] = relationship(
        "EstateEvent", back_populates="estate", order_by="EstateEvent.created_at"
    )


class EstateEvent(Base):
    """Append-only event log for an estate matter."""

    __tablename__ = "estate_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    estate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("estates.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(
        String(100), nullable=False, default="other", server_default="other"
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )
    estate: Mapped["Estate"] = relationship("Estate", back_populates="events")


class MediationCase(Base):
    """Mediation case — one row per mediation engagement."""

    __tablename__ = "mediation_cases"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    parties: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(
        String(100), default="active", server_default="active"
    )
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    events: Mapped[list["MediationCaseEvent"]] = relationship(
        "MediationCaseEvent",
        back_populates="case",
        order_by="MediationCaseEvent.created_at",
    )


class MediationCaseEvent(Base):
    """Append-only event log for a mediation case."""

    __tablename__ = "mediation_case_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mediation_cases.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(
        String(100), nullable=False, default="other", server_default="other"
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )
    case: Mapped["MediationCase"] = relationship(
        "MediationCase", back_populates="events"
    )
