"""Add durable marketing demo requests and first-party funnel events.

Revision ID: 111_marketing_demo_funnel
Revises: 110_chat_artifact_pipeline
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "111_marketing_demo_funnel"
down_revision = "110_chat_artifact_pipeline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "marketing_demo_requests",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("firm_name", sa.String(300), nullable=False),
        sa.Column("phone", sa.String(60), nullable=True),
        sa.Column("team_size", sa.String(50), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column(
            "source_path", sa.String(500), nullable=False, server_default="/demo"
        ),
        sa.Column("campaign", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="new"),
        sa.Column(
            "notification_status",
            sa.String(30),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "idx_marketing_demo_requests_created_at",
        "marketing_demo_requests",
        ["created_at"],
    )
    op.create_index(
        "idx_marketing_demo_requests_email", "marketing_demo_requests", ["email"]
    )
    op.create_index(
        "idx_marketing_demo_requests_status",
        "marketing_demo_requests",
        ["status", "created_at"],
    )

    op.create_table(
        "marketing_events",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("session_id", UUID(as_uuid=True), nullable=False),
        sa.Column("page", sa.String(500), nullable=False),
        sa.Column("properties", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "idx_marketing_events_name_created", "marketing_events", ["name", "created_at"]
    )
    op.create_index(
        "idx_marketing_events_session", "marketing_events", ["session_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("idx_marketing_events_session", table_name="marketing_events")
    op.drop_index("idx_marketing_events_name_created", table_name="marketing_events")
    op.drop_table("marketing_events")
    op.drop_index(
        "idx_marketing_demo_requests_status", table_name="marketing_demo_requests"
    )
    op.drop_index(
        "idx_marketing_demo_requests_email", table_name="marketing_demo_requests"
    )
    op.drop_index(
        "idx_marketing_demo_requests_created_at", table_name="marketing_demo_requests"
    )
    op.drop_table("marketing_demo_requests")
