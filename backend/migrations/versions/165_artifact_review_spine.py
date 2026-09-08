"""First-class artifact review requirements, approvals and delivery receipts."""

from alembic import op

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

    for statement in _statements(REVIEW_GUARDS_SQL):
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
    sql = "\n".join(
        line for line in sql.splitlines() if not line.lstrip().startswith("--")
    )
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


REVIEW_GUARDS_SQL = r"""
-- Frozen SQL for revision 165; do not edit after this migration is released.
ALTER TABLE tenant_settings ADD COLUMN artifact_review_policy varchar(30) NOT NULL DEFAULT 'staff_then_attorney';
ALTER TABLE tenant_settings ADD CONSTRAINT ck_tenant_artifact_review_policy CHECK (artifact_review_policy IN ('staff_then_attorney','attorney_only'));
ALTER TABLE tasks DROP CONSTRAINT ck_tasks_review_policy;
ALTER TABLE tasks ADD CONSTRAINT ck_tasks_review_policy CHECK (review_policy IN ('single','staff_then_attorney','attorney_only'));
ALTER TABLE tasks ADD CONSTRAINT ck_tasks_attorney_only_review CHECK (review_policy <> 'attorney_only' OR (attorney_reviewer_user_id IS NOT NULL AND reviewer_user_id IS NOT NULL AND reviewer_user_id = attorney_reviewer_user_id AND review_stage IN ('attorney_pending','approved') AND (review_stage <> 'approved' OR (attorney_approved_at IS NOT NULL AND attorney_approved_by_user_id IS NOT NULL))));

CREATE FUNCTION validate_work_artifact_approval() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
DECLARE a generated_artifacts; r generated_artifact_revisions; q work_artifact_review_requirement; d matter_documents;
BEGIN
  SELECT * INTO a FROM generated_artifacts WHERE tenant_id=NEW.tenant_id AND id=NEW.artifact_id FOR UPDATE;
  SELECT * INTO r FROM generated_artifact_revisions WHERE tenant_id=NEW.tenant_id AND artifact_id=NEW.artifact_id AND id=NEW.revision_id;
  SELECT * INTO q FROM work_artifact_review_requirement WHERE tenant_id=NEW.tenant_id AND id=NEW.requirement_id FOR UPDATE;
  SELECT * INTO d FROM matter_documents WHERE tenant_id=NEW.tenant_id AND id=NEW.document_id;
  IF a.id IS NULL OR r.id IS NULL OR q.id IS NULL OR d.id IS NULL
     OR a.current_revision_no <> r.revision_no OR a.status <> 'review'
     OR q.artifact_id <> a.id OR q.revision_id <> r.id OR q.status <> 'pending'
     OR r.content_sha256 <> NEW.content_sha256
     OR a.output_document_id IS DISTINCT FROM d.id
     OR d.generated_artifact_id IS DISTINCT FROM a.id
     OR d.generated_artifact_revision_id IS DISTINCT FROM r.id
     OR d.document_sha256 IS DISTINCT FROM NEW.document_sha256
     OR d.storage_state IS DISTINCT FROM 'verified' THEN
    RAISE EXCEPTION 'artifact review evidence is stale or invalid' USING ERRCODE='23514';
  END IF;
  IF NEW.decision = 'override' THEN
    IF q.reviewer_role <> 'staff' OR NOT EXISTS (
      SELECT 1 FROM work_artifact_review_requirement ar WHERE ar.tenant_id=NEW.tenant_id
      AND ar.artifact_id=a.id AND ar.revision_id=r.id AND ar.review_round=q.review_round
      AND ar.reviewer_role='attorney' AND ar.reviewer_user_id=NEW.reviewer_user_id AND ar.status='pending'
    ) THEN RAISE EXCEPTION 'invalid artifact staff override' USING ERRCODE='23514'; END IF;
  ELSE
    IF q.reviewer_user_id <> NEW.reviewer_user_id THEN
      RAISE EXCEPTION 'artifact reviewer mismatch' USING ERRCODE='23514';
    END IF;
    IF NEW.decision='approve' AND EXISTS (
      SELECT 1 FROM work_artifact_review_requirement prior WHERE prior.tenant_id=NEW.tenant_id
      AND prior.artifact_id=a.id AND prior.revision_id=r.id AND prior.review_round=q.review_round
      AND prior.sequence<q.sequence AND prior.required AND prior.status NOT IN ('approved','skipped')
    ) THEN RAISE EXCEPTION 'prior artifact review is required' USING ERRCODE='23514'; END IF;
  END IF;
  RETURN NEW;
END $$;

CREATE FUNCTION guard_work_artifact_requirement() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
BEGIN
  IF TG_OP='DELETE' THEN
    IF public.config_workflow_demo_purge_authorized(OLD.tenant_id) THEN RETURN OLD; END IF;
    RAISE EXCEPTION 'artifact review requirements cannot be deleted';
  END IF;
  IF (to_jsonb(OLD)-'status'-'superseded_at') IS DISTINCT FROM (to_jsonb(NEW)-'status'-'superseded_at')
     OR OLD.status='superseded' THEN RAISE EXCEPTION 'artifact review requirement is immutable'; END IF;
  IF NEW.status='superseded' AND NEW.superseded_at IS NOT NULL THEN RETURN NEW; END IF;
  IF OLD.status<>'pending' OR NEW.status NOT IN ('approved','changes_requested','skipped') OR NOT EXISTS (
    SELECT 1 FROM work_artifact_approval ap WHERE ap.tenant_id=NEW.tenant_id AND ap.requirement_id=NEW.id
    AND ap.decision=CASE NEW.status WHEN 'approved' THEN 'approve' WHEN 'changes_requested' THEN 'request_changes' ELSE 'override' END
  ) THEN RAISE EXCEPTION 'artifact requirement transition lacks decision evidence'; END IF;
  RETURN NEW;
END $$;

CREATE FUNCTION supersede_work_artifact_reviews() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
BEGIN
  IF OLD.current_revision_no<>NEW.current_revision_no THEN
    UPDATE work_artifact_review_requirement SET status='superseded', superseded_at=now()
    WHERE tenant_id=NEW.tenant_id AND artifact_id=NEW.id AND superseded_at IS NULL;
  END IF;
  RETURN NEW;
END $$;

CREATE FUNCTION validate_work_artifact_delivery() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
DECLARE ap work_artifact_approval; q work_artifact_review_requirement; attempt task_automation_runs; first_receipt work_artifact_delivery;
BEGIN
  SELECT * INTO attempt FROM task_automation_runs WHERE tenant_id=NEW.tenant_id AND id=NEW.attempt_id;
  IF attempt.id IS NULL OR attempt.action_type<>'email_client' OR NEW.channel<>'email'
     OR attempt.triggered_by_user_id IS DISTINCT FROM NEW.actor_user_id
     OR attempt.action_snapshot->'artifact_attachment'->>'approval_id' IS DISTINCT FROM NEW.approval_id::text
     OR attempt.action_snapshot->'artifact_attachment'->>'artifact_id' IS DISTINCT FROM NEW.artifact_id::text
     OR attempt.action_snapshot->'artifact_attachment'->>'revision_id' IS DISTINCT FROM NEW.revision_id::text
     OR attempt.action_snapshot->'artifact_attachment'->>'document_sha256' IS DISTINCT FROM NEW.document_sha256
     OR (attempt.action_snapshot->'recipient_bindings')::jsonb IS DISTINCT FROM NEW.recipient_bindings::jsonb THEN
    RAISE EXCEPTION 'delivery must match the immutable outbound attempt' USING ERRCODE='23514';
  END IF;
  IF NEW.status<>'queued' THEN
    IF (NEW.status='outcome_unknown' AND attempt.delivery_certainty_v2 IS DISTINCT FROM 'outcome_unknown')
       OR (NEW.status<>'outcome_unknown' AND attempt.status IS DISTINCT FROM NEW.status) THEN
      RAISE EXCEPTION 'delivery receipt disagrees with the recorded attempt' USING ERRCODE='23514';
    END IF;
    SELECT * INTO first_receipt FROM work_artifact_delivery WHERE tenant_id=NEW.tenant_id AND attempt_id=NEW.attempt_id AND status='queued';
    IF first_receipt.id IS NULL OR first_receipt.approval_id<>NEW.approval_id THEN
      RAISE EXCEPTION 'delivery outcome requires a queued receipt' USING ERRCODE='23514';
    END IF;
  END IF;
  SELECT * INTO ap FROM work_artifact_approval WHERE tenant_id=NEW.tenant_id AND id=NEW.approval_id;
  SELECT * INTO q FROM work_artifact_review_requirement WHERE tenant_id=NEW.tenant_id AND id=ap.requirement_id;
  IF ap.id IS NULL OR ap.decision<>'approve' OR q.reviewer_role<>'attorney'
     OR ap.document_sha256<>NEW.document_sha256 THEN
    RAISE EXCEPTION 'artifact delivery requires exact attorney approval' USING ERRCODE='23514';
  END IF;
  IF NEW.status='queued' AND (q.status<>'approved' OR EXISTS (
    SELECT 1 FROM generated_artifacts a JOIN generated_artifact_revisions r
    ON r.tenant_id=a.tenant_id AND r.artifact_id=a.id AND r.revision_no=a.current_revision_no
    WHERE a.tenant_id=NEW.tenant_id AND a.id=NEW.artifact_id AND r.id<>NEW.revision_id
  )) THEN RAISE EXCEPTION 'delivery approval was superseded' USING ERRCODE='23514'; END IF;
  IF NEW.channel IN ('email','sms') AND json_array_length(NEW.recipient_bindings)=0 THEN
    RAISE EXCEPTION 'delivery requires resolved recipient bindings' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
"""
