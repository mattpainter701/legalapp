"""Bind each tenant Zoom app to an explicit Zoom Account ID.

Revision ID: 090_zoom_account_binding
Revises: 089_zoom_phone_durability
"""

from alembic import op
import sqlalchemy as sa

revision = "090_zoom_account_binding"
down_revision = "089_zoom_phone_durability"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenant_oauth_apps",
        sa.Column("zoom_account_id", sa.String(length=255), nullable=True),
    )

    # Both tables are FORCE RLS. The migration owner is deliberately
    # NOBYPASSRLS, so make the same-tenant legacy mapping visible only for this
    # transactional backfill. Any failure rolls the transaction (including
    # these flags) back; the normal path restores FORCE before returning.
    op.execute("ALTER TABLE tenant_oauth_apps NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenant_credentials NO FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        UPDATE tenant_oauth_apps AS app
        SET zoom_account_id = NULLIF(BTRIM(credential.service_account_email), '')
        FROM tenant_credentials AS credential
        WHERE app.tenant_id = credential.tenant_id
          AND app.provider = 'zoom_phone'
          AND credential.provider = 'zoom_phone'
          AND app.zoom_account_id IS NULL
          AND NULLIF(BTRIM(credential.service_account_email), '') IS NOT NULL
        """
    )
    op.execute("ALTER TABLE tenant_credentials FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenant_oauth_apps FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_column("tenant_oauth_apps", "zoom_account_id")
