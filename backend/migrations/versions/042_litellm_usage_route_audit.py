"""042 - LiteLLM route audit fields on usage_records

Revision ID: 042
Revises: 041
Create Date: 2026-06-05
"""

from alembic import op
import sqlalchemy as sa

revision = "042"
down_revision = "041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "usage_records", sa.Column("requested_route", sa.String(50), nullable=True)
    )
    op.add_column(
        "usage_records", sa.Column("resolved_route", sa.String(50), nullable=True)
    )
    op.add_column(
        "usage_records", sa.Column("gateway_provider", sa.String(50), nullable=True)
    )
    op.add_column(
        "usage_records", sa.Column("gateway_alias", sa.String(200), nullable=True)
    )
    op.add_column(
        "usage_records", sa.Column("gateway_request_id", sa.String(200), nullable=True)
    )
    op.add_column(
        "usage_records",
        sa.Column("gateway_fallback_count", sa.Integer(), nullable=True),
    )
    op.add_column(
        "usage_records", sa.Column("final_provider", sa.String(100), nullable=True)
    )
    op.add_column(
        "usage_records", sa.Column("final_model", sa.String(200), nullable=True)
    )
    op.create_index(
        "ix_usage_records_route_created",
        "usage_records",
        ["resolved_route", "created_at"],
    )
    op.create_index(
        "ix_usage_records_gateway_alias_created",
        "usage_records",
        ["gateway_alias", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_usage_records_gateway_alias_created", table_name="usage_records")
    op.drop_index("ix_usage_records_route_created", table_name="usage_records")
    op.drop_column("usage_records", "final_model")
    op.drop_column("usage_records", "final_provider")
    op.drop_column("usage_records", "gateway_fallback_count")
    op.drop_column("usage_records", "gateway_request_id")
    op.drop_column("usage_records", "gateway_alias")
    op.drop_column("usage_records", "gateway_provider")
    op.drop_column("usage_records", "resolved_route")
    op.drop_column("usage_records", "requested_route")
