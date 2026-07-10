"""Add atomic Zoom Phone call idempotency.

Revision ID: 089_zoom_phone_durability
Revises: 088_scheduler_logs_rls
"""

from alembic import op
import sqlalchemy as sa

revision = "089_zoom_phone_durability"
down_revision = "088_scheduler_logs_rls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # communication_logs is FORCE RLS. The migrator is the table owner but is
    # deliberately NOBYPASSRLS, so temporarily remove FORCE only inside this
    # transactional migration to make the duplicate diagnostic truthful. Never
    # silently delete or merge customer communication rows.
    op.execute("ALTER TABLE communication_logs NO FORCE ROW LEVEL SECURITY")
    duplicate_groups = op.get_bind().scalar(
        sa.text(
            """
            SELECT count(*)
            FROM (
                SELECT tenant_id, external_ref
                FROM communication_logs
                WHERE external_ref LIKE 'zoom_phone:call:%'
                GROUP BY tenant_id, external_ref
                HAVING count(*) > 1
            ) AS duplicate_zoom_calls
            """
        )
    )
    op.execute("ALTER TABLE communication_logs FORCE ROW LEVEL SECURITY")
    if duplicate_groups:
        raise RuntimeError(
            "Cannot add Zoom Phone idempotency index: "
            f"{duplicate_groups} tenant-scoped duplicate call key(s) require "
            "operator review; no customer rows were changed."
        )

    op.create_index(
        "uq_commlogs_zoom_phone_external_ref",
        "communication_logs",
        ["tenant_id", "external_ref"],
        unique=True,
        postgresql_where=sa.text("external_ref LIKE 'zoom_phone:call:%'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_commlogs_zoom_phone_external_ref",
        table_name="communication_logs",
    )
