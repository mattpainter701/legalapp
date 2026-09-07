"""Tenant-scoped aliases with expiring proof and a tenant-safe user reference."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "161_user_alias_addresses"
down_revision = "160_cloud_provider_tiers"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "user_alias_addresses",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("address", sa.String(255), nullable=False),
        sa.Column("normalized_address", sa.String(255), nullable=False),
        sa.Column("is_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("verification_method", sa.String(64)),
        sa.Column("verification_token_hash", sa.String(64)),
        sa.Column("verification_expires_at", sa.DateTime(timezone=True)),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("tenant_id", "normalized_address", name="uq_user_alias_tenant_address"),
        sa.ForeignKeyConstraint(["tenant_id", "user_id"], ["users.tenant_id", "users.id"], name="fk_user_alias_tenant_user", ondelete="CASCADE"),
    )
    op.create_index("idx_user_alias_user", "user_alias_addresses", ["tenant_id", "user_id"])
    op.create_index("idx_user_alias_verified", "user_alias_addresses", ["tenant_id", "normalized_address", "is_verified"])
    op.execute("ALTER TABLE user_alias_addresses ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE user_alias_addresses FORCE ROW LEVEL SECURITY")
    predicate = "tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"
    op.execute(f"CREATE POLICY user_alias_addresses_tenant_isolation ON user_alias_addresses USING ({predicate}) WITH CHECK ({predicate})")
    # OAuth callbacks already enable this transaction-local, read-only auth
    # lookup mode for users. Only verified aliases participate in that lookup.
    op.execute("CREATE POLICY user_alias_addresses_auth_lookup ON user_alias_addresses FOR SELECT USING (is_verified AND current_setting('app.rls_bypass', true) = 'on')")



def downgrade():
    op.drop_table("user_alias_addresses")
