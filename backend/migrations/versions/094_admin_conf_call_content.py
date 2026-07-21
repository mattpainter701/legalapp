"""Grant confidential call content to existing Administrator roles.

Revision ID: 094_admin_conf_call_content
Revises: 093_conf_call_content
"""

from alembic import op


revision = "094_admin_conf_call_content"
down_revision = "093_conf_call_content"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE roles
        SET capabilities = capabilities || '["view_confidential_call_content"]'::jsonb
        WHERE is_system = true
          AND name = 'Administrator'
          AND NOT (capabilities @> '["view_confidential_call_content"]'::jsonb)
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE roles
        SET capabilities = capabilities - 'view_confidential_call_content'
        WHERE is_system = true AND name = 'Administrator'
        """
    )
