"""Retain a signer's hand-drawn signature image.

The portal lets a signer draw their signature as well as type their name. The
drawing is stamped onto the executed copy and kept on the signer row, hashed
into the evidence certificate. Nullable and additive: every existing signer
typed their name, and NULL means exactly that.

Revision ID: 183_drawn_signature
Revises: 182_unpublished_tpl_inactive
"""

import sqlalchemy as sa
from alembic import op

revision = "183_drawn_signature"
down_revision = "182_unpublished_tpl_inactive"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "signature_signers",
        sa.Column("drawn_signature_png", sa.LargeBinary(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("signature_signers", "drawn_signature_png")
