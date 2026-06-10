"""Add default matter_type for general matters."""

from alembic import op
import sqlalchemy as sa

revision = "048"
down_revision = "047"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column(
        "matters",
        "matter_type",
        existing_type=sa.String(length=100),
        nullable=False,
        server_default="general",
    )


def downgrade():
    op.alter_column(
        "matters",
        "matter_type",
        existing_type=sa.String(length=100),
        nullable=False,
        server_default=None,
    )
