"""Grant confidential call content to the default internal User role.

Revision ID: 093_conf_call_content
Revises: 092_zoom_phone_api_webhook_split
"""

from alembic import op


revision = "093_conf_call_content"
down_revision = "092_zoom_phone_api_webhook_split"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE roles
        SET capabilities = capabilities || '["view_confidential_call_content"]'::jsonb
        WHERE is_system = true
          AND name = 'User'
          AND NOT (capabilities @> '["view_confidential_call_content"]'::jsonb)
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE roles
        SET capabilities = capabilities - 'view_confidential_call_content'
        WHERE is_system = true AND name = 'User'
        """
    )
