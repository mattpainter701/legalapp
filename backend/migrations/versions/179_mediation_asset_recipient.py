"""Bind financial disclosure releases to an explicitly selected party.

Legacy sent entries have no trustworthy recipient evidence. Leave them hidden
from other parties until a new reviewed replacement is deliberately released.
"""

from alembic import op
import sqlalchemy as sa


revision = "179_mediation_asset_recipient"
down_revision = "178_intake_optional_agreement"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "mediation_assets", sa.Column("released_to_party_id", sa.UUID(), nullable=True)
    )
    op.add_column(
        "mediation_assets", sa.Column("released_by_user_id", sa.UUID(), nullable=True)
    )
    op.create_foreign_key(
        "fk_mediation_asset_release_party",
        "mediation_assets",
        "mediation_parties",
        ["released_to_party_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_mediation_asset_release_user",
        "mediation_assets",
        "users",
        ["released_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_mediation_assets_released_to_party_id",
        "mediation_assets",
        ["released_to_party_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_mediation_assets_released_to_party_id", table_name="mediation_assets"
    )
    op.drop_constraint(
        "fk_mediation_asset_release_user", "mediation_assets", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_mediation_asset_release_party", "mediation_assets", type_="foreignkey"
    )
    op.drop_column("mediation_assets", "released_by_user_id")
    op.drop_column("mediation_assets", "released_to_party_id")
