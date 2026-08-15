"""Add structured, verified professional user context.

Revision ID: 104_user_professional_context
Revises: 103_task_action_delivery_audit
"""

from alembic import op
import sqlalchemy as sa


revision = "104_user_professional_context"
down_revision = "103_task_action_delivery_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("professional_role", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("job_title", sa.String(length=160), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("office_location", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column(
            "primary_jurisdictions",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "primary_jurisdictions")
    op.drop_column("users", "office_location")
    op.drop_column("users", "job_title")
    op.drop_column("users", "professional_role")
