"""Capture approved workflow subscriptions in lifecycle source transactions.

Revision ID: 166_workflow_lifecycle_events
Revises: 165_artifact_review_spine
"""

from alembic import op

revision = "166_workflow_lifecycle_events"
down_revision = "165_artifact_review_spine"
branch_labels = None
depends_on = None

EVENTS = (
    "matter_created",
    "matter_stage_changed",
    "task_completed",
    "document_received",
    "intake_submitted",
    "esign_completed",
    "deadline_approaching",
    "invoice_overdue",
    "inbound_email_matched_to_matter",
    "payment_received",
)
TABLES = (
    "tasks",
    "matter_documents",
    "matter_intakes",
    "intake_submissions",
    "leads",
    "signature_requests",
    "inbound_emails",
    "payments",
)

CAPTURE_SQL = r"""
-- These functions run as the invoking tenant role; no SECURITY DEFINER bypass.
CREATE FUNCTION workflow_lifecycle_fingerprint(p_tenant uuid, p_matter uuid,
 p_kind text, p_source uuid, p_event text) RETURNS text
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE source_facts jsonb; matter_facts jsonb; fields jsonb;
BEGIN
 CASE p_kind
 WHEN 'task' THEN
   SELECT jsonb_build_object('status',status,'due_date',due_date,'completed_at',completed_at,
     'task_type',task_type,'assigned_to',assigned_to_user_id) INTO source_facts FROM tasks
   WHERE tenant_id=p_tenant AND matter_id=p_matter AND id=p_source
     AND ((p_event='task_completed' AND status='completed') OR
       (p_event='deadline_approaching' AND task_type='deadline'
         AND status NOT IN ('completed','cancelled') AND due_date BETWEEN current_date AND current_date+3));
 WHEN 'document' THEN
   SELECT jsonb_build_object('sha256',document_sha256,'etag',provider_etag,
     'version',provider_version_id,'state',storage_state,'path',storage_path) INTO source_facts
   FROM matter_documents WHERE tenant_id=p_tenant AND matter_id=p_matter AND id=p_source
     AND p_event='document_received' AND generated_artifact_id IS NULL
     AND (storage_state='verified' OR (storage_state IS NULL AND storage_path IS NOT NULL));
 WHEN 'matter_intake' THEN
   SELECT jsonb_build_object('answers',answers,'questionnaire',requirements->'questionnaire')
   INTO source_facts FROM matter_intakes WHERE tenant_id=p_tenant AND matter_id=p_matter
     AND id=p_source AND p_event='intake_submitted'
     AND requirements->'questionnaire'->>'completed'='true';
 WHEN 'intake_submission' THEN
   SELECT jsonb_build_object('answers',s.answers,'status',s.status,'lead_id',s.lead_id)
   INTO source_facts FROM intake_submissions s JOIN leads l
     ON l.id=s.lead_id AND l.tenant_id=s.tenant_id
   WHERE s.tenant_id=p_tenant AND l.matter_id=p_matter AND s.id=p_source
     AND s.status='accepted' AND p_event='intake_submitted';
 WHEN 'signature' THEN
   SELECT jsonb_build_object('status',status,'completed_at',completed_at,
     'sha256',completion_artifact_sha256,'evidence',evidence_sha256)
   INTO source_facts FROM signature_requests WHERE tenant_id=p_tenant AND matter_id=p_matter
     AND id=p_source AND status='completed' AND p_event='esign_completed';
 WHEN 'inbound_email' THEN
   SELECT jsonb_build_object('status',status,'reviewed_at',reviewed_at,
     'communication_log_id',communication_log_id)
   INTO source_facts FROM inbound_emails WHERE tenant_id=p_tenant AND matter_id=p_matter
     AND id=p_source AND status='accepted' AND p_event='inbound_email_matched_to_matter';
 WHEN 'payment' THEN
   SELECT jsonb_build_object('invoice',p.invoice_id,'amount',p.amount,'date',p.payment_date)
   INTO source_facts FROM payments p JOIN invoices i ON i.id=p.invoice_id AND i.tenant_id=p.tenant_id
   WHERE p.tenant_id=p_tenant AND i.matter_id=p_matter AND p.id=p_source AND p.amount>0
     AND p_event='payment_received';
 WHEN 'invoice' THEN
   SELECT jsonb_build_object('status',i.status,'due_date',i.due_date,'total',i.total,
     'paid',coalesce((SELECT sum(p.amount) FROM payments p WHERE p.tenant_id=p_tenant AND p.invoice_id=i.id),0))
   INTO source_facts FROM invoices i WHERE i.tenant_id=p_tenant AND i.matter_id=p_matter
     AND i.id=p_source AND p_event='invoice_overdue' AND i.due_date<current_date
     AND i.status IN ('sent','overdue','partially_paid')
     AND i.total>coalesce((SELECT sum(p.amount) FROM payments p WHERE p.tenant_id=p_tenant AND p.invoice_id=i.id),0);
 ELSE RETURN NULL;
 END CASE;
 IF source_facts IS NULL THEN RETURN NULL; END IF;
 SELECT jsonb_build_object('stage',stage,'type',matter_type,'practice_area',practice_area,
   'owner',user_id,'attorney',attorney_of_record_id,'archived_at',archived_at)
 INTO matter_facts FROM matters WHERE tenant_id=p_tenant AND id=p_matter;
 IF matter_facts IS NULL THEN RETURN NULL; END IF;
 SELECT coalesce(jsonb_agg(jsonb_build_object('id',d.id,'key',d.field_key,
   'schema',d.schema_version,'hmac',v.value_hmac) ORDER BY d.id),'[]'::jsonb) INTO fields
 FROM custom_field_definitions d LEFT JOIN matter_custom_field_values v
   ON v.tenant_id=d.tenant_id AND v.field_definition_id=d.id AND v.matter_id=p_matter
 WHERE d.tenant_id=p_tenant AND d.entity_type='matter' AND d.active;
 RETURN encode(public.digest(jsonb_build_object('source',source_facts,'matter',matter_facts,
   'fields',fields)::text,'sha256'),'hex');
END $$;

CREATE FUNCTION capture_workflow_lifecycle_event(p_tenant uuid,p_matter uuid,
 p_event text,p_kind text,p_source uuid,p_occurrence text) RETURNS integer
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE r record; fingerprint text; version_id uuid; event_key text; inserted integer; total integer:=0;
BEGIN
 IF p_matter IS NULL OR p_source IS NULL OR p_occurrence IS NULL THEN RETURN 0; END IF;
 IF p_event NOT IN ('task_completed','document_received','intake_submitted','esign_completed',
   'deadline_approaching','invoice_overdue','inbound_email_matched_to_matter','payment_received') THEN
   RAISE EXCEPTION 'Unsupported lifecycle event';
 END IF;
 -- Avoid adding locks or work to ordinary writes when no rule subscribes.
 IF NOT EXISTS (SELECT 1 FROM matter_workflow_automation_rules WHERE tenant_id=p_tenant
   AND trigger_event=p_event AND status='active') THEN RETURN 0; END IF;
 PERFORM pg_advisory_xact_lock_shared(1464224843,hashtext(p_tenant::text));
 fingerprint:=workflow_lifecycle_fingerprint(p_tenant,p_matter,p_kind,p_source,p_event);
 IF fingerprint IS NULL THEN RETURN 0; END IF;
 FOR r IN SELECT rule.* FROM matter_workflow_automation_rules rule JOIN matters m
   ON m.tenant_id=rule.tenant_id AND m.id=p_matter
   WHERE rule.tenant_id=p_tenant AND rule.trigger_event=p_event AND rule.status='active'
     AND m.archived_at IS NULL
     AND (rule.match_matter_type IS NULL OR lower(btrim(rule.match_matter_type))=lower(btrim(m.matter_type)))
     AND (rule.match_practice_area IS NULL OR lower(btrim(rule.match_practice_area))=lower(btrim(m.practice_area)))
   ORDER BY rule.id
 LOOP
   SELECT v.id INTO version_id FROM matter_workflow_template_versions v
   JOIN matter_workflow_templates t ON t.id=v.template_id AND t.tenant_id=v.tenant_id
   WHERE v.tenant_id=p_tenant AND v.template_id=r.template_id AND v.status='approved' AND t.active
   ORDER BY v.version DESC LIMIT 1;
   event_key:=encode(public.digest(jsonb_build_array(p_event,p_kind,p_source,p_occurrence)::text,'sha256'),'hex');
   INSERT INTO durable_jobs(id,tenant_id,kind,idempotency_key,payload,status,progress,attempts,max_attempts,available_at,created_at,updated_at)
   VALUES(gen_random_uuid(),p_tenant,'workflow_lifecycle_plan',r.id::text||':'||event_key,
     jsonb_build_object('rule_id',r.id,'matter_id',p_matter,'actor_user_id',r.activated_by_user_id,
       'trigger_event',p_event,'source_kind',p_kind,'source_id',p_source,'context_sha256',fingerprint,
       'rule_sha256',r.definition_sha256,'template_version_id',version_id,'as_of',current_date,'dedupe_key',event_key),
     'pending',0,0,5,now(),now(),now()) ON CONFLICT(tenant_id,kind,idempotency_key) DO NOTHING;
   GET DIAGNOSTICS inserted=ROW_COUNT;
   total:=total+inserted;
 END LOOP;
 RETURN total;
END $$;

CREATE FUNCTION emit_workflow_lifecycle_event() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE prior jsonb:='{}'::jsonb; current_row jsonb; matter uuid; submission record;
BEGIN
 current_row:=to_jsonb(NEW);
 IF TG_OP='UPDATE' THEN prior:=to_jsonb(OLD); END IF;
 matter:=(current_row->>'matter_id')::uuid;
 CASE TG_TABLE_NAME
 WHEN 'tasks' THEN
   IF NEW.status='completed' AND prior->>'status' IS DISTINCT FROM 'completed' THEN
     PERFORM capture_workflow_lifecycle_event(NEW.tenant_id,matter,'task_completed','task',NEW.id,
       coalesce(NEW.completed_at::text,NEW.version::text));
   END IF;
 WHEN 'matter_documents' THEN
   IF NEW.generated_artifact_id IS NULL AND (NEW.storage_state='verified' OR (NEW.storage_state IS NULL AND NEW.storage_path IS NOT NULL))
     AND NOT(coalesce(prior->>'storage_state'='verified',false) OR
       (prior->>'storage_state' IS NULL AND prior->>'storage_path' IS NOT NULL)) THEN
     PERFORM capture_workflow_lifecycle_event(NEW.tenant_id,matter,'document_received','document',NEW.id,'received');
   END IF;
 WHEN 'matter_intakes' THEN
   IF NEW.requirements->'questionnaire'->>'completed'='true'
     AND prior->'requirements'->'questionnaire'->>'completed' IS DISTINCT FROM 'true' THEN
     PERFORM capture_workflow_lifecycle_event(NEW.tenant_id,matter,'intake_submitted','matter_intake',NEW.id,'submitted');
   END IF;
 WHEN 'intake_submissions' THEN
   IF NEW.status='accepted' AND prior->>'status' IS DISTINCT FROM 'accepted' THEN
     SELECT l.matter_id INTO matter FROM leads l WHERE l.tenant_id=NEW.tenant_id AND l.id=NEW.lead_id;
     PERFORM capture_workflow_lifecycle_event(NEW.tenant_id,matter,'intake_submitted','intake_submission',NEW.id,'submitted');
   END IF;
 WHEN 'leads' THEN
   IF matter IS NOT NULL AND prior->>'matter_id' IS DISTINCT FROM matter::text THEN
     FOR submission IN SELECT id FROM intake_submissions WHERE tenant_id=NEW.tenant_id AND lead_id=NEW.id AND status='accepted' LOOP
       PERFORM capture_workflow_lifecycle_event(NEW.tenant_id,matter,'intake_submitted','intake_submission',submission.id,'submitted');
     END LOOP;
   END IF;
 WHEN 'signature_requests' THEN
   IF NEW.status='completed' AND prior->>'status' IS DISTINCT FROM 'completed' THEN
     PERFORM capture_workflow_lifecycle_event(NEW.tenant_id,matter,'esign_completed','signature',NEW.id,'completed');
   END IF;
 WHEN 'inbound_emails' THEN
   IF NEW.status='accepted' AND prior->>'status' IS DISTINCT FROM 'accepted' THEN
     PERFORM capture_workflow_lifecycle_event(NEW.tenant_id,matter,'inbound_email_matched_to_matter','inbound_email',NEW.id,'accepted');
   END IF;
 WHEN 'payments' THEN
   IF NEW.amount>0 AND (TG_OP='INSERT' OR coalesce((prior->>'amount')::numeric,0)<=0) THEN
     SELECT i.matter_id INTO matter FROM invoices i WHERE i.tenant_id=NEW.tenant_id AND i.id=NEW.invoice_id;
     PERFORM capture_workflow_lifecycle_event(NEW.tenant_id,matter,'payment_received','payment',NEW.id,'received');
   END IF;
 END CASE;
 RETURN NEW;
END $$;
"""


def _statements(source):
    source = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("--")
    )
    parts = source.split("$$")
    statement = ""
    for index, part in enumerate(parts):
        if index % 2:
            statement += "$$" + part + "$$"
            continue
        chunks = part.split(";")
        for chunk in chunks[:-1]:
            statement += chunk
            if statement.strip():
                yield statement.strip()
            statement = ""
        statement += chunks[-1]
    if statement.strip():
        yield statement.strip()


def _event_checks(events):
    allowed = ",".join("'" + event + "'" for event in events)
    for table in (
        "matter_workflow_automation_rules",
        "matter_workflow_automation_events",
    ):
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT ck_{table}_event")
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT ck_{table}_event CHECK (trigger_event IN ({allowed}))"
        )


def upgrade():
    _event_checks(EVENTS)
    for statement in _statements(CAPTURE_SQL):
        op.execute(statement)
    for table in TABLES:
        op.execute(
            f"CREATE CONSTRAINT TRIGGER workflow_lifecycle_capture AFTER INSERT OR UPDATE ON {table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION emit_workflow_lifecycle_event()"
        )


def downgrade():
    # Never erase subscribed rules or immutable history to force a downgrade.
    op.execute("""DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM matter_workflow_automation_rules WHERE trigger_event NOT IN ('matter_created','matter_stage_changed'))
        OR EXISTS (SELECT 1 FROM matter_workflow_automation_events WHERE trigger_event NOT IN ('matter_created','matter_stage_changed'))
        OR EXISTS (SELECT 1 FROM durable_jobs WHERE kind='workflow_lifecycle_plan') THEN
        RAISE EXCEPTION 'Lifecycle rules or history exist; preserve evidence and roll forward';
      END IF;
    END $$""")
    for table in TABLES:
        op.execute(f"DROP TRIGGER workflow_lifecycle_capture ON {table}")
    op.execute("DROP FUNCTION emit_workflow_lifecycle_event()")
    op.execute(
        "DROP FUNCTION capture_workflow_lifecycle_event(uuid,uuid,text,text,uuid,text)"
    )
    op.execute("DROP FUNCTION workflow_lifecycle_fingerprint(uuid,uuid,text,uuid,text)")
    _event_checks(EVENTS[:2])
