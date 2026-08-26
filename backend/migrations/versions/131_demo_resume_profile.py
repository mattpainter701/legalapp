"""Add safe demo resume lookup and a Platform-selected demo AI profile.

Revision ID: 131_demo_resume_profile
Revises: 130_smb_agent_bootstrap_rls
"""

from alembic import op
import sqlalchemy as sa


revision = "131_demo_resume_profile"
down_revision = "130_smb_agent_bootstrap_rls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "demo_sessions",
        sa.Column("resume_email_hash", sa.String(length=64), nullable=True),
    )
    op.execute(
        """
        UPDATE demo_sessions
        SET resume_email_hash = encode(
            digest(lower(btrim(prospect_email)), 'sha256'),
            'hex'
        )
        WHERE resume_email_hash IS NULL
        """
    )
    op.create_index(
        "ix_demo_sessions_resume_email_hash",
        "demo_sessions",
        ["resume_email_hash"],
    )
    op.execute(
        """
        CREATE POLICY demo_sessions_resume_lookup
        ON demo_sessions
        FOR SELECT TO PUBLIC
        USING (
            resume_email_hash = NULLIF(
                current_setting('app.demo_resume_email_hash', true),
                ''
            )
        )
        """
    )

    op.add_column(
        "llm_routing_profiles",
        sa.Column(
            "is_demo_default",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_index(
        "uq_llm_routing_profiles_demo_default",
        "llm_routing_profiles",
        ["is_demo_default"],
        unique=True,
        postgresql_where=sa.text("is_demo_default"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_llm_routing_profiles_demo_default",
        table_name="llm_routing_profiles",
    )
    op.drop_column("llm_routing_profiles", "is_demo_default")

    op.execute("DROP POLICY IF EXISTS demo_sessions_resume_lookup ON demo_sessions")
    op.drop_index("ix_demo_sessions_resume_email_hash", table_name="demo_sessions")
    op.drop_column("demo_sessions", "resume_email_hash")
