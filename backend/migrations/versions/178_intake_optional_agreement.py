"""Make the intake packet's fee-agreement signature optional.

A packet may now carry only the questionnaire, the client intake form, or the
requested uploads, so ``matter_intakes.signature_id`` has to accept NULL. The
portal invite (``invite_id``) stays required: the client still needs a link to
reach that paperwork. Widening the column is additive and keeps every existing
packet pointing at its signature.

Downgrade restores NOT NULL and will fail if a packet with no fee agreement
exists, because the previous shape could not represent one. Resolve or remove
those packets before downgrading.

Revision ID: 178_intake_optional_agreement
Revises: 177_matter_venue
"""

from sqlalchemy.dialects import postgresql
from alembic import op

revision = "178_intake_optional_agreement"
down_revision = "177_matter_venue"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column(
        "matter_intakes",
        "signature_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )


def downgrade():
    op.alter_column(
        "matter_intakes",
        "signature_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
