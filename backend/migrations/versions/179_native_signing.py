"""Native in-document signing.

Signing now happens inside the document in the client portal: the signer fills
the PDF's own fields and adopts a typed name at the signature line, or uploads
a signed copy for the firm to accept. The request therefore tracks the executed
(filled, signed, flattened) copy, a submitted upload awaiting review, and the
last failure to file the executed copy so a storage outage can be retried by
the scheduler without failing the client's signing action. Each signer keeps
the field values they entered and how they signed.

Every column is nullable and additive, so existing requests are untouched.

Revision ID: 179_native_signing
Revises: 178_intake_optional_agreement
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision = "179_native_signing"
down_revision = "178_intake_optional_agreement"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "signature_requests",
        sa.Column(
            "executed_document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "matter_documents.id",
                ondelete="SET NULL",
                name="fk_signature_requests_executed_document",
            ),
            nullable=True,
        ),
    )
    op.add_column(
        "signature_requests",
        sa.Column(
            "submitted_document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "matter_documents.id",
                ondelete="SET NULL",
                name="fk_signature_requests_submitted_document",
            ),
            nullable=True,
        ),
    )
    op.add_column(
        "signature_requests",
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "signature_requests",
        sa.Column("completion_error", sa.Text(), nullable=True),
    )
    op.add_column(
        "signature_requests",
        sa.Column("completion_attempted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "signature_requests",
        sa.Column("signing_plan", sa.JSON(), nullable=True),
    )
    op.add_column(
        "signature_signers",
        sa.Column("field_values", sa.JSON(), nullable=True),
    )
    op.add_column(
        "signature_signers",
        sa.Column("method", sa.String(length=40), nullable=True),
    )


def downgrade():
    op.drop_column("signature_signers", "method")
    op.drop_column("signature_signers", "field_values")
    op.drop_column("signature_requests", "signing_plan")
    op.drop_column("signature_requests", "completion_attempted_at")
    op.drop_column("signature_requests", "completion_error")
    op.drop_column("signature_requests", "submitted_at")
    op.drop_constraint(
        "fk_signature_requests_submitted_document",
        "signature_requests",
        type_="foreignkey",
    )
    op.drop_column("signature_requests", "submitted_document_id")
    op.drop_constraint(
        "fk_signature_requests_executed_document",
        "signature_requests",
        type_="foreignkey",
    )
    op.drop_column("signature_requests", "executed_document_id")
