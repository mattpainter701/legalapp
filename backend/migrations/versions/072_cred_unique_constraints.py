"""072 - widen user_oauth_tokens uniqueness to include tenant_id

Revision ID: 072_cred_unique_constraints
Revises: 071_operator_audit_logs
Create Date: 2026-07-02

tenant_credentials already has a unique index on (tenant_id, provider)
(``ix_tenant_credentials_tenant_provider``, added in migration 009), so
scalar_one_or_none() lookups there cannot raise MultipleResultsFound — no
change needed for that table.

user_oauth_tokens only has a unique index on (user_id, provider)
(``ix_user_oauth_tokens_user_provider``, also from migration 009). That's
functionally sufficient today (a user belongs to exactly one tenant) but
doesn't make the tenant scoping explicit at the DB level. Widen it to
(tenant_id, user_id, provider) so the invariant is self-documenting and
matches every query pattern in the app, which always filters by all three.
"""

from alembic import op

revision = "072_cred_unique_constraints"
down_revision = "071_operator_audit_logs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Defensive no-op dedupe: the existing unique index on tenant_credentials
    # already prevents duplicates from being inserted, but guard against any
    # data that reached this table outside normal app writes.
    op.execute(
        """
        DELETE FROM tenant_credentials tc
        USING (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY tenant_id, provider
                       ORDER BY is_active DESC, updated_at DESC, created_at DESC
                   ) AS rn
            FROM tenant_credentials
        ) ranked
        WHERE tc.id = ranked.id
          AND ranked.rn > 1
        """
    )

    op.drop_index("ix_user_oauth_tokens_user_provider", table_name="user_oauth_tokens")
    op.create_index(
        "ix_user_oauth_tokens_tenant_user_provider",
        "user_oauth_tokens",
        ["tenant_id", "user_id", "provider"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_user_oauth_tokens_tenant_user_provider", table_name="user_oauth_tokens"
    )
    op.create_index(
        "ix_user_oauth_tokens_user_provider",
        "user_oauth_tokens",
        ["user_id", "provider"],
        unique=True,
    )
