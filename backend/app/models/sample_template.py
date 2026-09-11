"""SampleTemplate model — platform-owned, read-only template catalog.

This table is deliberately *not* row-level-security scoped. Unlike tenant data,
the sample library is shared content: every authenticated tenant may read the
same rows, and no tenant may mutate them. Writes happen only through the
``scripts/seed_sample_templates.py`` operator script. Tenants get their own
``DocumentTemplate`` rows only when they choose to customize a sample.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, JSON, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SampleTemplate(Base):
    __tablename__ = "sample_templates"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    slug: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    category: Mapped[str] = mapped_column(
        String(50), default="other", server_default="other", nullable=False
    )
    jurisdictions: Mapped[list | None] = mapped_column(JSON, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    format: Mapped[str] = mapped_column(
        String(50), default="pdf", server_default="pdf", nullable=False
    )
    source_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_file_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    field_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    variable_schema: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
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
