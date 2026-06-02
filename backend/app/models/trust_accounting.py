"""Trust accounting models — IOLTA trust accounts with three-way reconciliation."""

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class TrustAccount(Base):
    """IOLTA or pooled trust account for a client/matter.

    Represents a segregated trust ledger. Each trust account maps to
    one client (or matter) and tracks the running balance of funds
    held on behalf of that client.
    """

    __tablename__ = "trust_accounts"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "matter_id", name="uq_trust_accounts_tenant_matter"
        ),
        Index("idx_trust_accounts_tenant_id", "tenant_id"),
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
        ForeignKey("matters.id", ondelete="RESTRICT"),
        nullable=False,
    )
    account_name: Mapped[str] = mapped_column(String(300), nullable=False)
    bank_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    account_number_masked: Mapped[str | None] = mapped_column(
        String(10), nullable=True, comment="Last 4 digits only"
    )
    current_balance: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        default=0,
        server_default="0",
        comment="Running balance from posted trust transactions",
    )
    minimum_balance: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2),
        nullable=True,
        comment="Minimum threshold for evergreen retainer auto-replenishment",
    )
    auto_replenish_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    auto_replenish_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2),
        nullable=True,
        comment="Amount to replenish when balance drops below minimum",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    transactions: Mapped[list["TrustTransaction"]] = relationship(
        "TrustTransaction",
        back_populates="trust_account",
        order_by="TrustTransaction.transaction_date.desc(), TrustTransaction.created_at.desc()",
    )


class TrustTransaction(Base):
    """Individual entry in a trust account ledger.

    Every deposit, disbursement, transfer, or replenishment is recorded
    here. The running balance on TrustAccount is a cache — the
    authoritative source is the sum of all posted transactions.
    """

    __tablename__ = "trust_transactions"
    __table_args__ = (
        Index("idx_trust_transactions_account_id", "trust_account_id"),
        Index("idx_trust_transactions_tenant_id", "tenant_id"),
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
    trust_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("trust_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    transaction_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="deposit, disbursement, transfer_in, transfer_out, replenishment, fee, adjustment",
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    transaction_date: Mapped[datetime] = mapped_column(
        Date,
        nullable=False,
        default=lambda: datetime.now(timezone.utc).date(),
    )
    reference_number: Mapped[str | None] = mapped_column(String(200), nullable=True)
    check_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    is_reconciled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    reconciled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )

    # Relationships
    trust_account: Mapped["TrustAccount"] = relationship(
        "TrustAccount", back_populates="transactions"
    )
