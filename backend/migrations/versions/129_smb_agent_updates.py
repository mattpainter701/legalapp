"""Track tenant-administered SMB agent updates."""

from alembic import op
import sqlalchemy as sa

revision = "129_smb_agent_updates"
down_revision = "128_workspace_mcp_tenant_gate"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "smb_agents",
        sa.Column(
            "update_status", sa.String(20), nullable=False, server_default="idle"
        ),
    )
    op.add_column(
        "smb_agents", sa.Column("update_target_version", sa.String(50), nullable=True)
    )
    op.add_column(
        "smb_agents", sa.Column("update_manifest_id", sa.String(200), nullable=True)
    )
    op.add_column(
        "smb_agents", sa.Column("update_task_id", sa.String(128), nullable=True)
    )
    op.add_column(
        "smb_agents",
        sa.Column("update_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "smb_agents",
        sa.Column("update_completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "smb_agents", sa.Column("update_error", sa.String(2000), nullable=True)
    )


def downgrade() -> None:
    for name in (
        "update_error",
        "update_completed_at",
        "update_requested_at",
        "update_task_id",
        "update_manifest_id",
        "update_target_version",
        "update_status",
    ):
        op.drop_column("smb_agents", name)
