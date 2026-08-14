"""Hash MCP API keys at rest: add api_key_hash + api_key_prefix, migrate, null plaintext.

Revision ID: 058
Revises: 057
Create Date: 2026-06-16

Security rationale
------------------
``tenants.api_key`` is a long-lived MCP bearer credential that was previously
stored and compared in plaintext (``SELECT ... WHERE api_key = :val``). A DB
dump would expose all active keys verbatim. This migration:

  1. Adds ``api_key_hash VARCHAR(64)`` — SHA-256 hex digest of the raw key.
  2. Adds ``api_key_prefix VARCHAR(8)`` — first 8 chars of the raw key for
     display masking (same pattern as the existing LLM provider key hint).
  3. Back-fills both columns for every existing tenant that has a key, using
     Postgres's built-in ``sha256()`` (available since PG 11) so the raw value
     never leaves the database during migration.
  4. Nulls out the plaintext ``api_key`` column on migrated rows.

The application is updated in the same deploy to:
  - Look up by ``api_key_hash = sha256(incoming_key)`` instead of plaintext.
  - Store only hash + prefix on regeneration.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "058"
down_revision: Union[str, None] = "057"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("api_key_hash", sa.String(64), nullable=True, unique=True),
    )
    op.add_column(
        "tenants",
        sa.Column("api_key_prefix", sa.String(8), nullable=True),
    )
    op.create_index(
        "ix_tenants_api_key_hash", "tenants", ["api_key_hash"], unique=True
    )

    # Back-fill: hash existing plaintext keys in-DB, then null out the plaintext.
    # sha256() returns bytea; encode(..., 'hex') gives a 64-char hex string.
    op.execute(
        """
        UPDATE tenants
        SET api_key_hash = encode(sha256(api_key::bytea), 'hex'),
            api_key_prefix = left(api_key, 8),
            api_key = NULL
        WHERE api_key IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_index("ix_tenants_api_key_hash", table_name="tenants")
    op.drop_column("tenants", "api_key_prefix")
    op.drop_column("tenants", "api_key_hash")
