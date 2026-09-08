"""Retain immutable configuration-synthesis evidence on ordinary drafts.

Revision ID: 167_workflow_synthesis
Revises: 166_workflow_lifecycle_events
"""

from alembic import op

revision = "167_workflow_synthesis"
down_revision = "166_workflow_lifecycle_events"
branch_labels = None
depends_on = None

DDL = (
    "\nCREATE TABLE workflow_configuration_proposals (\n\tid UUID NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tpattern_key VARCHAR(64) NOT NULL, \n\tproposal_sha256 VARCHAR(64) NOT NULL, \n\tdefinition_sha256 VARCHAR(64) NOT NULL, \n\ttemplate_id UUID NOT NULL, \n\ttemplate_version_id UUID NOT NULL, \n\trule_id UUID NOT NULL, \n\tsource_job_id UUID NOT NULL, \n\tstatus VARCHAR(20) DEFAULT 'pending' NOT NULL, \n\tconfiguration_json JSONB NOT NULL, \n\tevidence_json JSONB NOT NULL, \n\tbaseline_json JSONB, \n\tcreated_by_user_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\trejected_by_user_id UUID, \n\trejected_at TIMESTAMP WITH TIME ZONE, \n\trejection_reason TEXT, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_workflow_configuration_proposals_tenant UNIQUE (tenant_id, id), \n\tCONSTRAINT uq_workflow_configuration_proposals_basis UNIQUE (tenant_id, pattern_key, proposal_sha256), \n\tCONSTRAINT ck_workflow_configuration_proposals_status CHECK (status IN ('pending','rejected')), \n\tCONSTRAINT ck_workflow_configuration_proposals_hashes CHECK (pattern_key ~ '^[a-f0-9]{64}$' AND proposal_sha256 ~ '^[a-f0-9]{64}$' AND definition_sha256 ~ '^[a-f0-9]{64}$'), \n\tCONSTRAINT ck_workflow_configuration_proposals_rejection CHECK ((status='pending' AND rejected_by_user_id IS NULL AND rejected_at IS NULL AND rejection_reason IS NULL) OR (status='rejected' AND rejected_by_user_id IS NOT NULL AND rejected_at IS NOT NULL AND rejection_reason IS NOT NULL AND length(btrim(rejection_reason))>0)), \n\tFOREIGN KEY(tenant_id, template_id) REFERENCES matter_workflow_templates (tenant_id, id) ON DELETE RESTRICT, \n\tFOREIGN KEY(tenant_id, template_version_id) REFERENCES matter_workflow_template_versions (tenant_id, id) ON DELETE RESTRICT, \n\tFOREIGN KEY(tenant_id, rule_id) REFERENCES matter_workflow_automation_rules (tenant_id, id) ON DELETE RESTRICT, \n\tFOREIGN KEY(tenant_id, created_by_user_id) REFERENCES users (tenant_id, id) ON DELETE RESTRICT, \n\tFOREIGN KEY(tenant_id, rejected_by_user_id) REFERENCES users (tenant_id, id) ON DELETE RESTRICT, \n\tFOREIGN KEY(tenant_id, source_job_id) REFERENCES durable_jobs (tenant_id, id) ON DELETE RESTRICT, \n\tFOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE\n)\n\n",
    "CREATE INDEX ix_workflow_configuration_proposals_tenant_created ON workflow_configuration_proposals (tenant_id, created_at)",
)

GUARDS_SQL = r"""
CREATE FUNCTION guard_workflow_configuration_proposal() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE version_status text; version_hash text;
BEGIN
 IF TG_OP='DELETE' THEN
   IF public.config_workflow_demo_purge_authorized(OLD.tenant_id) THEN RETURN OLD; END IF;
   RAISE EXCEPTION 'Workflow proposal evidence cannot be deleted';
 END IF;
 SELECT status,definition_sha256 INTO version_status,version_hash
 FROM matter_workflow_template_versions WHERE tenant_id=NEW.tenant_id
   AND id=NEW.template_version_id AND template_id=NEW.template_id FOR SHARE;
 IF TG_OP='INSERT' THEN
   IF NEW.status<>'pending' OR version_status IS DISTINCT FROM 'draft'
     OR version_hash IS DISTINCT FROM NEW.definition_sha256 THEN
     RAISE EXCEPTION 'Proposal must bind an exact ordinary draft version';
   END IF;
   IF NOT EXISTS (SELECT 1 FROM matter_workflow_automation_rules r
     WHERE r.tenant_id=NEW.tenant_id AND r.id=NEW.rule_id AND r.template_id=NEW.template_id
       AND (r.status='draft' OR (r.status='active' AND NEW.baseline_json IS NOT NULL))) THEN
     RAISE EXCEPTION 'Proposal rule must be draft or an explicit amendment baseline';
   END IF;
   IF NOT EXISTS (SELECT 1 FROM durable_jobs j WHERE j.tenant_id=NEW.tenant_id
     AND j.id=NEW.source_job_id AND j.kind='workflow_configuration_synthesis'
     AND j.status='running' AND j.payload->>'actor_user_id'=NEW.created_by_user_id::text) THEN
     RAISE EXCEPTION 'Proposal must retain its authenticated synthesis job';
   END IF;
   RETURN NEW;
 END IF;
 IF (to_jsonb(NEW)-ARRAY['status','rejected_by_user_id','rejected_at','rejection_reason'])
   IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['status','rejected_by_user_id','rejected_at','rejection_reason']) THEN
   RAISE EXCEPTION 'Workflow proposal identity and evidence are immutable';
 END IF;
 IF OLD.status<>'pending' OR NEW.status<>'rejected' OR version_status<>'draft' THEN
   RAISE EXCEPTION 'Only an unapproved pending proposal can be declined';
 END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION guard_proposed_workflow_version() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE proposal record; current_approved uuid;
BEGIN
 SELECT * INTO proposal FROM workflow_configuration_proposals
 WHERE tenant_id=OLD.tenant_id AND template_version_id=OLD.id FOR SHARE;
 IF NOT FOUND THEN
   IF TG_OP='DELETE' THEN RETURN OLD; END IF;
   RETURN NEW;
 END IF;
 IF TG_OP='DELETE' THEN
   IF public.config_workflow_demo_purge_authorized(OLD.tenant_id) THEN RETURN OLD; END IF;
   RAISE EXCEPTION 'Proposed version retains immutable evidence';
 END IF;
 IF NEW.definition_sha256 IS DISTINCT FROM proposal.definition_sha256 THEN
   RAISE EXCEPTION 'Edit a proposed workflow by creating another draft version';
 END IF;
 IF OLD.status='draft' AND NEW.status='approved' THEN
   IF proposal.status='rejected' THEN RAISE EXCEPTION 'Declined proposal cannot be approved'; END IF;
   IF proposal.baseline_json IS NOT NULL THEN
     SELECT id INTO current_approved FROM matter_workflow_template_versions
     WHERE tenant_id=OLD.tenant_id AND template_id=OLD.template_id AND status='approved'
     ORDER BY version DESC LIMIT 1;
     IF current_approved::text IS DISTINCT FROM proposal.baseline_json->>'version_id' THEN
       RAISE EXCEPTION 'Approved workflow changed after this amendment was proposed';
     END IF;
   END IF;
 END IF;
 RETURN NEW;
END $$;
"""


def upgrade():
    for statement in DDL:
        op.execute(statement)
    op.execute("ALTER TABLE workflow_configuration_proposals ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE workflow_configuration_proposals FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON workflow_configuration_proposals USING (tenant_id=nullif(current_setting('app.current_tenant_id',true),'')::uuid) WITH CHECK (tenant_id=nullif(current_setting('app.current_tenant_id',true),'')::uuid)"
    )
    for statement in GUARDS_SQL.split("END $$;"):
        if statement.strip():
            op.execute(statement.strip() + "END $$;")
    op.execute(
        "CREATE TRIGGER workflow_configuration_proposal_guard BEFORE INSERT OR UPDATE OR DELETE ON workflow_configuration_proposals FOR EACH ROW EXECUTE FUNCTION guard_workflow_configuration_proposal()"
    )
    op.execute(
        "CREATE TRIGGER proposed_workflow_version_guard BEFORE UPDATE OR DELETE ON matter_workflow_template_versions FOR EACH ROW EXECUTE FUNCTION guard_proposed_workflow_version()"
    )


def downgrade():
    op.execute("""DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM workflow_configuration_proposals) THEN
        RAISE EXCEPTION 'Configuration proposal evidence exists; preserve it and roll forward';
      END IF;
    END $$""")
    op.execute(
        "DROP TRIGGER proposed_workflow_version_guard ON matter_workflow_template_versions"
    )
    op.execute("DROP TABLE workflow_configuration_proposals")
    op.execute("DROP FUNCTION guard_workflow_configuration_proposal()")
    op.execute("DROP FUNCTION guard_proposed_workflow_version()")
