"""Permit only credential-bound SMB agent bootstrap lookups."""

from alembic import op

revision = "130_smb_agent_bootstrap_rls"
down_revision = "129_smb_agent_updates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # This is SELECT-only and matches one presented credential exactly.  It
    # intentionally does not use the global RLS escape hatch and does not affect any other
    # SMB table or write operation.  The application clears both selectors
    # immediately after the lookup and binds the discovered tenant before any
    # subsequent read/write.
    op.execute(
        """
        CREATE POLICY smb_agent_bootstrap_lookup
        ON smb_agents
        FOR SELECT TO PUBLIC
        USING (
            (
                NULLIF(current_setting('app.smb_agent_api_key_hash', true), '')
                IS NOT NULL
                AND api_key_hash = current_setting('app.smb_agent_api_key_hash', true)
            )
            OR (
                NULLIF(current_setting('app.smb_agent_pairing_code', true), '')
                IS NOT NULL
                AND pairing_code = current_setting('app.smb_agent_pairing_code', true)
            )
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS smb_agent_bootstrap_lookup ON smb_agents")
