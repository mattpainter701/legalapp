"""Add office-ready metadata to e-signature requests.

Revision ID: 080
Revises: 079_error_logs_system_policy
Create Date: 2026-07-08
"""

from alembic import op
import sqlalchemy as sa


revision = "080"
down_revision = "079_error_logs_system_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "signature_requests",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "signature_requests",
        sa.Column("reminders", sa.JSON(), nullable=True),
    )
    op.add_column(
        "signature_requests",
        sa.Column(
            "enforce_signing_order",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "signature_requests",
        sa.Column("declined_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "signature_requests",
        sa.Column("decline_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "signature_requests",
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "signature_requests",
        sa.Column("void_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "signature_signers",
        sa.Column(
            "role",
            sa.String(100),
            nullable=False,
            server_default=sa.text("'signer'"),
        ),
    )
    op.add_column(
        "signature_signers",
        sa.Column("declined_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "signature_signers",
        sa.Column("decline_reason", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("signature_signers", "decline_reason")
    op.drop_column("signature_signers", "declined_at")
    op.drop_column("signature_signers", "role")
    op.drop_column("signature_requests", "void_reason")
    op.drop_column("signature_requests", "voided_at")
    op.drop_column("signature_requests", "decline_reason")
    op.drop_column("signature_requests", "declined_at")
    op.drop_column("signature_requests", "enforce_signing_order")
    op.drop_column("signature_requests", "reminders")
    op.drop_column("signature_requests", "expires_at")
