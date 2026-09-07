"""Role navigation profiles and account-synced personal layout."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "159_navigation_profiles"
down_revision = "158_matter_intakes"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("roles", sa.Column("navigation_paths", JSONB(), nullable=True))
    op.add_column(
        "users", sa.Column("navigation_preferences", sa.JSON(), nullable=True)
    )


def downgrade():
    op.drop_column("users", "navigation_preferences")
    op.drop_column("roles", "navigation_paths")
