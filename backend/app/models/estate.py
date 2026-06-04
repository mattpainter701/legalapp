"""Trust & Estate administration sub-entity models.

The ``Estate`` and ``EstateEvent`` models live in ``app.models.plugin`` (the
original skeleton). These are the administration sub-entities that hang off an
estate: fiduciaries, beneficiaries, asset/liability inventory, distributions,
deadlines, and the fiduciary accounting ledger. All are tenant-isolated via RLS
(see migration 030) and cascade-delete with their parent estate.
"""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID

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


class EstateFiduciary(Base):
    """Executor, trustee, personal representative, attorney, CPA, etc."""

    __tablename__ = "estate_fiduciaries"

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    estate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("estates.id", ondelete="CASCADE"), nullable=False
    )
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    role: Mapped[str] = mapped_column(
        String(50), default="executor", server_default="executor"
    )
    appointment_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_primary: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    compensation_basis: Mapped[str | None] = mapped_column(String(100), nullable=True)
    compensation_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    email: Mapped[str | None] = mapped_column(String(300), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    estate: Mapped["Estate"] = relationship("Estate", back_populates="fiduciaries")  # noqa: F821


class EstateBeneficiary(Base):
    """A beneficiary of the estate or trust."""

    __tablename__ = "estate_beneficiaries"

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    estate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("estates.id", ondelete="CASCADE"), nullable=False
    )
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    relationship_to_estate: Mapped[str | None] = mapped_column(
        "relationship", String(150), nullable=True
    )
    beneficiary_type: Mapped[str] = mapped_column(
        String(50), default="residuary", server_default="residuary"
    )
    share_percentage: Mapped[Decimal | None] = mapped_column(Numeric(7, 4), nullable=True)
    bequest_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_charity: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    charity_ein: Mapped[str | None] = mapped_column(String(20), nullable=True)
    email: Mapped[str | None] = mapped_column(String(300), nullable=True)
    address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    distribution_status: Mapped[str] = mapped_column(
        String(50), default="pending", server_default="pending"
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    estate: Mapped["Estate"] = relationship("Estate", back_populates="beneficiaries")  # noqa: F821


class EstateAsset(Base):
    """An asset (or holding) in the estate inventory."""

    __tablename__ = "estate_assets"

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    estate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("estates.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(400), nullable=False)
    category: Mapped[str] = mapped_column(
        String(50), default="other", server_default="other"
    )
    ownership_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    date_of_death_value: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 2), nullable=True
    )
    current_value: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    is_probate: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )
    institution: Mapped[str | None] = mapped_column(String(300), nullable=True)
    account_number_masked: Mapped[str | None] = mapped_column(String(10), nullable=True)
    valuation_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    location: Mapped[str | None] = mapped_column(String(300), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    estate: Mapped["Estate"] = relationship("Estate", back_populates="assets")  # noqa: F821


class EstateLiability(Base):
    """A creditor claim, debt, or administration expense against the estate."""

    __tablename__ = "estate_liabilities"

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    estate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("estates.id", ondelete="CASCADE"), nullable=False
    )
    creditor_name: Mapped[str] = mapped_column(String(400), nullable=False)
    claim_type: Mapped[str] = mapped_column(
        String(50), default="debt", server_default="debt"
    )
    amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), default=0, server_default="0"
    )
    date_filed: Mapped[date | None] = mapped_column(Date, nullable=True)
    bar_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(
        String(50), default="pending", server_default="pending"
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    estate: Mapped["Estate"] = relationship("Estate", back_populates="liabilities")  # noqa: F821


class EstateDistribution(Base):
    """A planned or completed distribution to a beneficiary."""

    __tablename__ = "estate_distributions"

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    estate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("estates.id", ondelete="CASCADE"), nullable=False
    )
    beneficiary_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("estate_beneficiaries.id", ondelete="CASCADE"),
        nullable=False,
    )
    asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("estate_assets.id", ondelete="SET NULL"),
        nullable=True,
    )
    amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), default=0, server_default="0"
    )
    distribution_type: Mapped[str] = mapped_column(
        String(50), default="interim", server_default="interim"
    )
    distribution_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(
        String(50), default="planned", server_default="planned"
    )
    check_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    estate: Mapped["Estate"] = relationship("Estate", back_populates="distributions")  # noqa: F821


class EstateDeadline(Base):
    """A tax filing, court deadline, or administration task with a due date."""

    __tablename__ = "estate_deadlines"

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    estate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("estates.id", ondelete="CASCADE"), nullable=False
    )
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
    reminder_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    estate: Mapped["Estate"] = relationship("Estate", back_populates="deadlines")  # noqa: F821


class EstateAccountingEntry(Base):
    """A single entry in the fiduciary accounting ledger.

    Each entry is classified by type (receipt/disbursement/gain/loss/distribution)
    and account class (income vs principal). Running balances are computed from the
    sum of posted entries.
    """

    __tablename__ = "estate_accounting_entries"

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    estate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("estates.id", ondelete="CASCADE"), nullable=False
    )
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    entry_type: Mapped[str] = mapped_column(
        String(50), default="receipt", server_default="receipt"
    )
    account_class: Mapped[str] = mapped_column(
        String(50), default="principal", server_default="principal"
    )
    amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), default=0, server_default="0"
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    payee_payor: Mapped[str | None] = mapped_column(String(300), nullable=True)
    asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("estate_assets.id", ondelete="SET NULL"),
        nullable=True,
    )
    reference_number: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    estate: Mapped["Estate"] = relationship(
        "Estate", back_populates="accounting_entries"
    )  # noqa: F821
