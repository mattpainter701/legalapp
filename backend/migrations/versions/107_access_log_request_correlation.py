"""correlate api access logs with request ids

Additive and nullable: existing rows keep a NULL request_id and stay valid.
Without this column an operator holding the request_id from a customer's failed
response can find the error row but not the request that produced it.

Revision ID: 107_access_log_request_correlation
Revises: 106_demo_usage_reservations
"""

from alembic import op
import sqlalchemy as sa

revision = "107_access_log_request_correlation"
down_revision = "106_demo_usage_reservations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "api_access_logs",
        sa.Column("request_id", sa.String(length=100), nullable=True),
    )
    op.create_index(
        "ix_api_access_logs_request_id",
        "api_access_logs",
        ["request_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_api_access_logs_request_id", table_name="api_access_logs")
    op.drop_column("api_access_logs", "request_id")
