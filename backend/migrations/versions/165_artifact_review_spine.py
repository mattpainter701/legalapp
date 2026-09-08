"""First-class artifact review requirements, approvals and delivery receipts."""

from alembic import op
from pathlib import Path

revision = "165_artifact_review_spine"
down_revision = "164_word_derived_source_evidence"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "\nCREATE TABLE work_artifact_review_requirement (\n\treview_round INTEGER NOT NULL, \n\tsequence INTEGER NOT NULL, \n\treviewer_role VARCHAR(20) NOT NULL, \n\treviewer_user_id UUID NOT NULL, \n\trequired BOOLEAN NOT NULL, \n\tstatus VARCHAR(24) NOT NULL, \n\tsuperseded_at TIMESTAMP WITH TIME ZONE, \n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tartifact_id UUID NOT NULL, \n\trevision_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(tenant_id, artifact_id, revision_id) REFERENCES generated_artifact_revisions (tenant_id, artifact_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_artifact_requirement_binding UNIQUE (tenant_id, artifact_id, revision_id, id), \n\tCONSTRAINT uq_artifact_requirement_round UNIQUE (tenant_id, artifact_id, revision_id, review_round, sequence), \n\tFOREIGN KEY(tenant_id, reviewer_user_id) REFERENCES users (tenant_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_artifact_requirement_sequence CHECK (sequence > 0 AND review_round > 0), \n\tCONSTRAINT ck_artifact_requirement_role CHECK (reviewer_role IN ('staff', 'attorney')), \n\tCONSTRAINT ck_artifact_requirement_status CHECK (status IN ('pending', 'approved', 'changes_requested', 'skipped', 'superseded')), \n\tCONSTRAINT ck_artifact_requirement_superseded CHECK ((status = 'superseded') = (superseded_at IS NOT NULL))\n)\n\n"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_artifact_requirement_current ON work_artifact_review_requirement (tenant_id, artifact_id, revision_id, reviewer_role) WHERE superseded_at IS NULL"
    )
    op.execute(
        "\nCREATE TABLE work_artifact_approval (\n\trequirement_id UUID NOT NULL, \n\treviewer_user_id UUID NOT NULL, \n\tdocument_id UUID NOT NULL, \n\tdecision VARCHAR(24) NOT NULL, \n\treason TEXT, \n\tcontent_sha256 VARCHAR(64) NOT NULL, \n\tdocument_sha256 VARCHAR(64) NOT NULL, \n\tpurpose VARCHAR(40) NOT NULL, \n\tstamp_metadata JSON NOT NULL, \n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tartifact_id UUID NOT NULL, \n\trevision_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(tenant_id, artifact_id, revision_id) REFERENCES generated_artifact_revisions (tenant_id, artifact_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_artifact_approval_binding UNIQUE (tenant_id, artifact_id, revision_id, id), \n\tCONSTRAINT uq_artifact_approval_requirement UNIQUE (requirement_id), \n\tFOREIGN KEY(tenant_id, artifact_id, revision_id, requirement_id) REFERENCES work_artifact_review_requirement (tenant_id, artifact_id, revision_id, id) ON DELETE RESTRICT, \n\tFOREIGN KEY(tenant_id, reviewer_user_id) REFERENCES users (tenant_id, id) ON DELETE RESTRICT, \n\tFOREIGN KEY(tenant_id, document_id) REFERENCES matter_documents (tenant_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_artifact_approval_decision CHECK (decision IN ('approve', 'request_changes', 'override')), \n\tCONSTRAINT ck_artifact_approval_override_reason CHECK (decision <> 'override' OR (reason IS NOT NULL AND length(btrim(reason)) > 0)), \n\tCONSTRAINT ck_artifact_approval_hashes CHECK (content_sha256 ~ '^[a-f0-9]{64}$' AND document_sha256 ~ '^[a-f0-9]{64}$')\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE work_artifact_delivery (\n\tapproval_id UUID NOT NULL, \n\tactor_user_id UUID NOT NULL, \n\tattempt_id UUID NOT NULL, \n\tchannel VARCHAR(20) NOT NULL, \n\tstatus VARCHAR(24) NOT NULL, \n\tdocument_sha256 VARCHAR(64) NOT NULL, \n\trecipient_bindings JSON NOT NULL, \n\tprovider_message_id VARCHAR(500), \n\tdetail JSON NOT NULL, \n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tartifact_id UUID NOT NULL, \n\trevision_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(tenant_id, artifact_id, revision_id) REFERENCES generated_artifact_revisions (tenant_id, artifact_id, id) ON DELETE RESTRICT, \n\tFOREIGN KEY(tenant_id, artifact_id, revision_id, approval_id) REFERENCES work_artifact_approval (tenant_id, artifact_id, revision_id, id) ON DELETE RESTRICT, \n\tFOREIGN KEY(tenant_id, actor_user_id) REFERENCES users (tenant_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_artifact_delivery_attempt_state UNIQUE (tenant_id, attempt_id, status), \n\tCONSTRAINT ck_artifact_delivery_status CHECK (status IN ('queued', 'submitted', 'sent', 'failed', 'outcome_unknown')), \n\tCONSTRAINT ck_artifact_delivery_channel CHECK (channel IN ('email', 'sms', 'filing')), \n\tCONSTRAINT ck_artifact_delivery_hash CHECK (document_sha256 ~ '^[a-f0-9]{64}$')\n)\n\n"
    )
    op.execute(
        "CREATE INDEX ix_artifact_delivery_timeline ON work_artifact_delivery (tenant_id, artifact_id, created_at)"
    )

    for statement in _statements(
        Path(__file__)
        .with_name("artifact_review_spine_guards.sql")
        .read_text(encoding="utf-8")
    ):
        op.execute(statement)
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON {table} USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)"
        )
    for table in TABLES[1:]:
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION prevent_config_workflow_immutable()"
        )
    op.execute(
        "CREATE TRIGGER work_artifact_approval_validate BEFORE INSERT ON work_artifact_approval FOR EACH ROW EXECUTE FUNCTION validate_work_artifact_approval()"
    )
    op.execute(
        "CREATE TRIGGER work_artifact_requirement_guard BEFORE UPDATE OR DELETE ON work_artifact_review_requirement FOR EACH ROW EXECUTE FUNCTION guard_work_artifact_requirement()"
    )
    op.execute(
        "CREATE TRIGGER work_artifact_revision_supersede AFTER UPDATE OF current_revision_no ON generated_artifacts FOR EACH ROW EXECUTE FUNCTION supersede_work_artifact_reviews()"
    )
    op.execute(
        "CREATE TRIGGER work_artifact_delivery_validate BEFORE INSERT ON work_artifact_delivery FOR EACH ROW EXECUTE FUNCTION validate_work_artifact_delivery()"
    )


TABLES = (
    "work_artifact_review_requirement",
    "work_artifact_approval",
    "work_artifact_delivery",
)


def _statements(sql):
    # The frozen script uses only $$ function bodies and semicolon statements.
    pending = ""
    for index, part in enumerate(sql.split("$$")):
        if index % 2:
            pending += "$$" + part + "$$"
            continue
        fragments = part.split(";")
        for fragment in fragments[:-1]:
            pending += fragment
            if pending.strip():
                yield pending.strip()
            pending = ""
        pending += fragments[-1]
    if pending.strip():
        yield pending.strip()


def downgrade():
    op.execute("ALTER TABLE tasks DROP CONSTRAINT ck_tasks_attorney_only_review")
    op.execute(
        "DROP TRIGGER IF EXISTS work_artifact_revision_supersede ON generated_artifacts"
    )
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE {table}")
    for function in (
        "validate_work_artifact_delivery",
        "supersede_work_artifact_reviews",
        "guard_work_artifact_requirement",
        "validate_work_artifact_approval",
    ):
        op.execute(f"DROP FUNCTION {function}()")
    op.execute("ALTER TABLE tenant_settings DROP COLUMN artifact_review_policy")
    # Existing attorney-only tasks remain readable using the legacy single path.
    op.execute(
        "UPDATE tasks SET review_policy='single' WHERE review_policy='attorney_only'"
    )
    op.execute("ALTER TABLE tasks DROP CONSTRAINT ck_tasks_review_policy")
    op.execute(
        "ALTER TABLE tasks ADD CONSTRAINT ck_tasks_review_policy CHECK (review_policy IN ('single','staff_then_attorney'))"
    )
