"""Add staged staff-to-attorney review metadata to tasks.

Revision ID: 115_staged_task_review
Revises: 114_generated_artifacts
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "115_staged_task_review"
down_revision = "114_generated_artifacts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "review_policy", sa.String(30), nullable=False, server_default="single"
        ),
    )
    op.add_column(
        "tasks",
        sa.Column(
            "review_stage", sa.String(30), nullable=False, server_default="attorney"
        ),
    )
    for name in (
        "staff_reviewer_user_id",
        "attorney_reviewer_user_id",
        "staff_reviewed_by_user_id",
        "attorney_approved_by_user_id",
    ):
        op.add_column(
            "tasks",
            sa.Column(
                name,
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )
    op.add_column(
        "tasks",
        sa.Column("staff_reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column("attorney_approved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column(
            "attorney_override",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_check_constraint(
        "ck_tasks_review_policy",
        "tasks",
        "review_policy IN ('single', 'staff_then_attorney')",
    )
    op.create_check_constraint(
        "ck_tasks_review_stage",
        "tasks",
        "review_stage IN ('attorney', 'staff', 'attorney_pending', 'approved')",
    )
    op.create_check_constraint(
        "ck_tasks_staged_reviewers_required",
        "tasks",
        "review_policy != 'staff_then_attorney' OR "
        "(staff_reviewer_user_id IS NOT NULL AND "
        "attorney_reviewer_user_id IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_tasks_staged_reviewers_distinct",
        "tasks",
        "review_policy != 'staff_then_attorney' OR "
        "staff_reviewer_user_id != attorney_reviewer_user_id",
    )
    op.create_check_constraint(
        "ck_tasks_review_evidence_pairs",
        "tasks",
        "(staff_reviewed_at IS NULL) = "
        "(staff_reviewed_by_user_id IS NULL) AND "
        "(attorney_approved_at IS NULL) = "
        "(attorney_approved_by_user_id IS NULL)",
    )
    op.create_check_constraint(
        "ck_tasks_staff_reviewer_evidence_actor",
        "tasks",
        "staff_reviewed_by_user_id IS NULL OR "
        "staff_reviewed_by_user_id = staff_reviewer_user_id",
    )
    op.create_check_constraint(
        "ck_tasks_attorney_reviewer_evidence_actor",
        "tasks",
        "attorney_approved_by_user_id IS NULL OR attorney_approved_by_user_id = attorney_reviewer_user_id",
    )
    op.create_check_constraint(
        "ck_tasks_staff_stage_reviewer",
        "tasks",
        "review_policy != 'staff_then_attorney' OR review_stage != 'staff' OR "
        "(reviewer_user_id IS NOT NULL AND "
        "reviewer_user_id = staff_reviewer_user_id)",
    )
    op.create_check_constraint(
        "ck_tasks_attorney_stage_reviewer",
        "tasks",
        "review_policy != 'staff_then_attorney' OR "
        "review_stage != 'attorney_pending' OR "
        "(reviewer_user_id IS NOT NULL AND "
        "reviewer_user_id = attorney_reviewer_user_id)",
    )
    op.create_check_constraint(
        "ck_tasks_approved_staff_evidence",
        "tasks",
        "review_policy != 'staff_then_attorney' OR review_stage != 'approved' OR "
        "attorney_override OR (staff_reviewed_at IS NOT NULL AND "
        "staff_reviewed_by_user_id = staff_reviewer_user_id)",
    )
    op.create_check_constraint(
        "ck_tasks_staff_review_evidence",
        "tasks",
        "review_policy != 'staff_then_attorney' OR "
        "review_stage != 'attorney_pending' OR "
        "(staff_reviewed_at IS NOT NULL AND staff_reviewed_by_user_id IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_tasks_attorney_approval_evidence",
        "tasks",
        "review_policy != 'staff_then_attorney' OR review_stage != 'approved' OR "
        "(attorney_approved_at IS NOT NULL AND "
        "attorney_approved_by_user_id IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_tasks_attorney_override_evidence",
        "tasks",
        "NOT attorney_override OR "
        "(review_policy = 'staff_then_attorney' AND review_stage = 'approved' "
        "AND attorney_approved_at IS NOT NULL "
        "AND attorney_approved_by_user_id IS NOT NULL)",
    )
    op.create_index(
        "idx_tasks_tenant_review_stage",
        "tasks",
        ["tenant_id", "review_policy", "review_stage"],
    )

    op.execute(
        "UPDATE roles SET capabilities = capabilities || '[\"approve_legal_work\"]'::jsonb WHERE name = 'Administrator' AND is_system IS TRUE AND NOT (capabilities @> '[\"approve_legal_work\"]'::jsonb)"
    )


def downgrade() -> None:
    op.drop_index("idx_tasks_tenant_review_stage", table_name="tasks")
    op.drop_constraint("ck_tasks_attorney_override_evidence", "tasks", type_="check")
    op.drop_constraint("ck_tasks_attorney_approval_evidence", "tasks", type_="check")
    op.drop_constraint("ck_tasks_staff_review_evidence", "tasks", type_="check")
    op.drop_constraint("ck_tasks_approved_staff_evidence", "tasks", type_="check")
    op.drop_constraint("ck_tasks_attorney_stage_reviewer", "tasks", type_="check")
    op.drop_constraint("ck_tasks_staff_stage_reviewer", "tasks", type_="check")
    op.drop_constraint(
        "ck_tasks_attorney_reviewer_evidence_actor", "tasks", type_="check"
    )
    op.drop_constraint("ck_tasks_staff_reviewer_evidence_actor", "tasks", type_="check")
    op.drop_constraint("ck_tasks_review_evidence_pairs", "tasks", type_="check")
    op.drop_constraint("ck_tasks_staged_reviewers_distinct", "tasks", type_="check")
    op.drop_constraint("ck_tasks_staged_reviewers_required", "tasks", type_="check")
    op.drop_constraint("ck_tasks_review_stage", "tasks", type_="check")
    op.drop_constraint("ck_tasks_review_policy", "tasks", type_="check")
    op.drop_column("tasks", "attorney_override")
    op.drop_column("tasks", "attorney_approved_at")
    op.drop_column("tasks", "staff_reviewed_at")
    for name in (
        "attorney_approved_by_user_id",
        "staff_reviewed_by_user_id",
        "attorney_reviewer_user_id",
        "staff_reviewer_user_id",
    ):
        op.drop_column("tasks", name)
    op.drop_column("tasks", "review_stage")
    op.drop_column("tasks", "review_policy")
