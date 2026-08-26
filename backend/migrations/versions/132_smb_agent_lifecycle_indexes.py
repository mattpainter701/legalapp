"""Index SMB agent authentication and lifecycle lookups.

Revision ID: 132_smb_agent_lifecycle_indexes
Revises: 131_demo_resume_profile
"""

from alembic import op


revision = "132_smb_agent_lifecycle_indexes"
down_revision = "131_demo_resume_profile"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # API-key authentication is the hot path for heartbeats, task polls, and
    # file sync. Pairing placeholders deliberately share the sentinel value
    # "pending", so this is non-unique.
    op.create_index(
        "ix_smb_agents_api_key_hash",
        "smb_agents",
        ["api_key_hash"],
        unique=False,
    )
    op.create_index(
        "ix_smb_agents_tenant_status_expiry",
        "smb_agents",
        ["tenant_id", "status", "pairing_expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_smb_agents_tenant_status_expiry", table_name="smb_agents")
    op.drop_index("ix_smb_agents_api_key_hash", table_name="smb_agents")
