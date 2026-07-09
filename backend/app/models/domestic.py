"""Domestic relations (family law) module models.

A ``DomesticCase`` is the parent entity for a family-law matter (divorce,
custody, support, paternity, protective order, modification). Hanging off it are
parties, children, custody arrangements, support orders, the payment ledger,
saved child-support calculation runs, deadlines, and an activity log. All are
tenant-isolated via RLS (see migration 051) and cascade-delete with the case.

The child-support calculation engine lives in ``app.services.childsupport``;
``ChildSupportCalculation`` persists a single run (input snapshot + worksheet +
schedule version) so results are reproducible and auditable.
"""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )


def _created_at() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )


def _updated_at() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
        onupdate=lambda: datetime.now(timezone.utc),
    )


def _case_fk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True),
        ForeignKey("domestic_cases.id", ondelete="CASCADE"),
        nullable=False,
    )


class DomesticCase(Base):
    """One family-law matter."""

    __tablename__ = "domestic_cases"

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    case_name: Mapped[str] = mapped_column(String(500), nullable=False)
    case_type: Mapped[str] = mapped_column(
        String(50), default="support", server_default="support"
    )
    status: Mapped[str] = mapped_column(
        String(50), default="active", server_default="active"
    )
    is_deleted: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Jurisdiction drives which guideline the calculator applies.
    jurisdiction: Mapped[str] = mapped_column(
        String(2), default="ND", server_default="ND"
    )
    county: Mapped[str | None] = mapped_column(String(120), nullable=True)
    court_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    case_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    filed_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    served_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Optional links to a Matter (billing/IOLTA/documents) and the client Contact.
    matter_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("matters.id", ondelete="SET NULL"), nullable=True
    )
    client_contact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("contacts.id", ondelete="SET NULL"),
        nullable=True,
    )

    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    matter: Mapped["Matter | None"] = relationship(  # noqa: F821
        "Matter", foreign_keys=[matter_id]
    )
    client: Mapped["Contact | None"] = relationship(  # noqa: F821
        "Contact", foreign_keys=[client_contact_id], lazy="joined"
    )
    parties: Mapped[list["DomesticParty"]] = relationship(
        "DomesticParty", back_populates="case", cascade="all, delete-orphan"
    )
    children: Mapped[list["DomesticChild"]] = relationship(
        "DomesticChild", back_populates="case", cascade="all, delete-orphan"
    )
    custody_arrangements: Mapped[list["CustodyArrangement"]] = relationship(
        "CustodyArrangement", back_populates="case", cascade="all, delete-orphan"
    )
    support_orders: Mapped[list["SupportOrder"]] = relationship(
        "SupportOrder", back_populates="case", cascade="all, delete-orphan"
    )
    calculations: Mapped[list["ChildSupportCalculation"]] = relationship(
        "ChildSupportCalculation", back_populates="case", cascade="all, delete-orphan"
    )
    deadlines: Mapped[list["DomesticDeadline"]] = relationship(
        "DomesticDeadline", back_populates="case", cascade="all, delete-orphan"
    )
    events: Mapped[list["DomesticEvent"]] = relationship(
        "DomesticEvent",
        back_populates="case",
        cascade="all, delete-orphan",
        order_by="DomesticEvent.created_at",
    )


class DomesticParty(Base):
    """A parent / guardian / party to the case, with an income snapshot."""

    __tablename__ = "domestic_parties"

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    case_id: Mapped[uuid.UUID] = _case_fk()
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("contacts.id", ondelete="SET NULL"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    role: Mapped[str] = mapped_column(
        String(50), default="respondent", server_default="respondent"
    )
    is_client: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )

    # Income snapshot (current best figures; calculations snapshot their own copy).
    gross_monthly_income: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    federal_income_tax: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    state_income_tax: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    fica_tax: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    required_retirement: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=0, server_default="0"
    )
    union_dues: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=0, server_default="0"
    )
    health_insurance_children: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=0, server_default="0"
    )
    existing_support_paid: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=0, server_default="0"
    )
    other_children_in_home: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    is_imputed: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    imputed_basis: Mapped[str | None] = mapped_column(String(100), nullable=True)
    annual_overnights: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    email: Mapped[str | None] = mapped_column(String(300), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    case: Mapped["DomesticCase"] = relationship(
        "DomesticCase", back_populates="parties"
    )


class DomesticChild(Base):
    """A child covered by the case."""

    __tablename__ = "domestic_children"

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    case_id: Mapped[uuid.UUID] = _case_fk()
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    date_of_birth: Mapped[date | None] = mapped_column(Date, nullable=True)
    primary_residence_party_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("domestic_parties.id", ondelete="SET NULL"),
        nullable=True,
    )
    has_special_needs: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    case: Mapped["DomesticCase"] = relationship(
        "DomesticCase", back_populates="children"
    )


class CustodyArrangement(Base):
    """Legal/physical custody + parenting-time facts that drive the calculation."""

    __tablename__ = "custody_arrangements"

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    case_id: Mapped[uuid.UUID] = _case_fk()
    legal_custody: Mapped[str] = mapped_column(
        String(50), default="joint", server_default="joint"
    )
    physical_custody: Mapped[str] = mapped_column(
        String(50), default="primary", server_default="primary"
    )
    # primary | equal | split — maps to the engine's CustodyType.
    calc_custody_type: Mapped[str] = mapped_column(
        String(20), default="primary", server_default="primary"
    )
    primary_party_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("domestic_parties.id", ondelete="SET NULL"),
        nullable=True,
    )
    children_with_party_a: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    schedule_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    case: Mapped["DomesticCase"] = relationship(
        "DomesticCase", back_populates="custody_arrangements"
    )


class SupportOrder(Base):
    """An entered (or proposed) child-support order with arrears tracking."""

    __tablename__ = "support_orders"

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    case_id: Mapped[uuid.UUID] = _case_fk()
    obligor_party_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("domestic_parties.id", ondelete="SET NULL"),
        nullable=True,
    )
    obligee_party_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("domestic_parties.id", ondelete="SET NULL"),
        nullable=True,
    )
    calculation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("child_support_calculations.id", ondelete="SET NULL"),
        nullable=True,
    )
    monthly_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=0, server_default="0"
    )
    frequency: Mapped[str] = mapped_column(
        String(20), default="monthly", server_default="monthly"
    )
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    arrears_balance: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=0, server_default="0"
    )
    status: Mapped[str] = mapped_column(
        String(50), default="proposed", server_default="proposed"
    )
    order_type: Mapped[str] = mapped_column(
        String(50), default="child_support", server_default="child_support"
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    case: Mapped["DomesticCase"] = relationship(
        "DomesticCase", back_populates="support_orders"
    )
    payments: Mapped[list["SupportPayment"]] = relationship(
        "SupportPayment", back_populates="order", cascade="all, delete-orphan"
    )


class SupportPayment(Base):
    """A single payment against a support order (the payment ledger)."""

    __tablename__ = "support_payments"

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    case_id: Mapped[uuid.UUID] = _case_fk()
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("support_orders.id", ondelete="CASCADE"),
        nullable=False,
    )
    payment_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=0, server_default="0"
    )
    applied_to_current: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=0, server_default="0"
    )
    applied_to_arrears: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=0, server_default="0"
    )
    method: Mapped[str | None] = mapped_column(String(50), nullable=True)
    reference_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    order: Mapped["SupportOrder"] = relationship(
        "SupportOrder", back_populates="payments"
    )


class ChildSupportCalculation(Base):
    """A persisted child-support calculation run (input + worksheet snapshot)."""

    __tablename__ = "child_support_calculations"

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    case_id: Mapped[uuid.UUID] = _case_fk()
    label: Mapped[str | None] = mapped_column(String(300), nullable=True)
    jurisdiction: Mapped[str] = mapped_column(
        String(2), default="ND", server_default="ND"
    )
    model_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    schedule_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    num_children: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    obligor_role: Mapped[str | None] = mapped_column(String(50), nullable=True)

    presumptive_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=0, server_default="0"
    )
    final_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=0, server_default="0"
    )
    deviation_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    deviation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Full snapshots so a run is reproducible and the worksheet re-renderable.
    input_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    worksheet: Mapped[dict] = mapped_column(JSONB, nullable=False)

    is_final: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    case: Mapped["DomesticCase"] = relationship(
        "DomesticCase", back_populates="calculations"
    )


class DomesticDeadline(Base):
    """A hearing, filing deadline, or exchange date for the case."""

    __tablename__ = "domestic_deadlines"

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    case_id: Mapped[uuid.UUID] = _case_fk()
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    deadline_type: Mapped[str] = mapped_column(
        String(50), default="other", server_default="other"
    )
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(
        String(50), default="pending", server_default="pending"
    )
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    case: Mapped["DomesticCase"] = relationship(
        "DomesticCase", back_populates="deadlines"
    )


class DomesticEvent(Base):
    """An activity-log entry for the case."""

    __tablename__ = "domestic_events"

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    case_id: Mapped[uuid.UUID] = _case_fk()
    event_type: Mapped[str] = mapped_column(
        String(50), default="note", server_default="note"
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created_at()

    case: Mapped["DomesticCase"] = relationship("DomesticCase", back_populates="events")
