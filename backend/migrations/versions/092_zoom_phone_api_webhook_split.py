"""Separate Zoom Phone API readiness from webhook account binding.

Revision ID: 092_zoom_phone_api_webhook_split
Revises: 091_pdf_preview_evidence
"""

from alembic import op


revision = "092_zoom_phone_api_webhook_split"
down_revision = "091_pdf_preview_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Both source tables use FORCE RLS. The owner-role migrator must temporarily
    # relax FORCE inside this transactional migration to repair every tenant;
    # PostgreSQL rolls the ALTERs back too if any statement fails.
    op.execute("ALTER TABLE tenant_oauth_apps NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenant_credentials NO FORCE ROW LEVEL SECURITY")

    # Every grant left in account_verification_required came from the regressed
    # administrator-entered workflow: Zoom did not return an account id and no
    # exact provider fetch ever promoted the mapping. Clear both copies even if
    # the submitted value was alphanumeric; the next signed exact-call proof can
    # establish the real opaque provider binding without reauthorization.
    op.execute(
        """
        UPDATE tenant_oauth_apps AS app
        SET zoom_account_id = NULL
        FROM tenant_credentials AS credential
        WHERE app.tenant_id = credential.tenant_id
          AND app.provider = 'zoom_phone'
          AND credential.provider = 'zoom_phone'
          AND credential.health = 'account_verification_required'
        """
    )

    op.execute(
        """
        UPDATE tenant_credentials
        SET service_account_email = NULL
        WHERE provider = 'zoom_phone'
          AND health = 'account_verification_required'
        """
    )

    # The retired UI instructed administrators to enter the numeric Account
    # Number shown in Zoom Account Profile. Zoom's API/webhook account_id is a
    # different opaque identifier, so numeric-only values are not bindings and
    # would otherwise make the first valid signed event fail permanently.
    op.execute(
        """
        UPDATE tenant_oauth_apps
        SET zoom_account_id = NULL
        WHERE provider = 'zoom_phone'
          AND BTRIM(COALESCE(zoom_account_id, '')) ~ '^[0-9]+$'
        """
    )

    op.execute(
        """
        UPDATE tenant_credentials
        SET service_account_email = NULL
        WHERE provider = 'zoom_phone'
          AND BTRIM(COALESCE(service_account_email, '')) ~ '^[0-9]+$'
        """
    )

    # account_verification_required was an artificial gate on an otherwise
    # valid refreshable Phone API grant. API health is now independent of the
    # optional webhook binding learned from a signed, exact-call event.
    op.execute(
        """
        UPDATE tenant_credentials
        SET health = 'healthy',
            last_refresh_error = NULL
        WHERE provider = 'zoom_phone'
          AND health = 'account_verification_required'
          AND is_active
          AND encrypted_refresh_token IS NOT NULL
        """
    )

    op.execute("ALTER TABLE tenant_credentials FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenant_oauth_apps FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    # The discarded numeric Account Number was never a valid provider binding,
    # and reintroducing a blocking state would break usable OAuth grants.
    pass
