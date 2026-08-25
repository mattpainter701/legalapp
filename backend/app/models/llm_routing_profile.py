"""Reusable operator-managed AI routing profiles."""

import uuid
from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, Index, JSON, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class LLMRoutingProfile(Base):
    __tablename__ = "llm_routing_profiles"
    __table_args__ = (
        Index(
            "uq_llm_routing_profiles_default",
            "is_default",
            unique=True,
            postgresql_where=text("is_default"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    name: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    standard_route: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    premium_route: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    standard_allow_matter_context: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    premium_allow_matter_context: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    activation: Mapped[dict | None] = mapped_column(JSON, nullable=True)
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

    @property
    def assignable(self) -> bool:
        activation = self.activation if isinstance(self.activation, dict) else {}
        aliases = (
            activation.get("aliases")
            if isinstance(activation.get("aliases"), dict)
            else {}
        )
        return bool(
            self.is_active
            and activation.get("status") == "active"
            and aliases.get("standard")
            and aliases.get("premium")
        )
