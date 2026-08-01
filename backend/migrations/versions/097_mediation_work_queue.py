"""Add operational work-queue fields to mediation cases.

Revision ID: 097_mediation_work_queue
Revises: 096_document_index_freshness
"""

from alembic import op
import sqlalchemy as sa


revision = "097_mediation_work_queue"
down_revision = "096_document_index_freshness"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("mediation_cases", sa.Column("jurisdiction", sa.String(300)))
    op.add_column("mediation_cases", sa.Column("court", sa.String(300)))
    op.add_column("mediation_cases", sa.Column("case_number", sa.String(100)))
    op.add_column("mediation_cases", sa.Column("waiting_on", sa.String(300)))
    op.add_column("mediation_cases", sa.Column("fixed_fee", sa.Numeric(12, 2)))
    op.create_index(
        "idx_mediation_cases_tenant_stage",
        "mediation_cases",
        ["tenant_id", "mediation_stage"],
    )


def downgrade() -> None:
    op.drop_index("idx_mediation_cases_tenant_stage", table_name="mediation_cases")
    op.drop_column("mediation_cases", "fixed_fee")
    op.drop_column("mediation_cases", "waiting_on")
    op.drop_column("mediation_cases", "case_number")
    op.drop_column("mediation_cases", "court")
    op.drop_column("mediation_cases", "jurisdiction")
