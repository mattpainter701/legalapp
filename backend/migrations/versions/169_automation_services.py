"""Approval-gated unattended service identities and immutable occurrence evidence.

Revision ID: 169_automation_services
Revises: 168_workflow_runtime
"""

from alembic import op


revision = "169_automation_services"
down_revision = "168_workflow_runtime"
branch_labels = None
depends_on = None


TABLES = (
    "automation_service_identities",
    "automation_service_rules",
    "automation_service_occurrences",
)


def upgrade():
    for statement in """
      ALTER TABLE users ADD COLUMN principal_type varchar(20) NOT NULL DEFAULT 'human';
      ALTER TABLE users ADD CONSTRAINT ck_users_principal_type
        CHECK (principal_type IN ('human','automation_service'));
      ALTER TABLE workflow_runs ADD COLUMN service_rule_id uuid;
      ALTER TABLE workflow_runs DROP CONSTRAINT ck_workflow_runs_channel;
      ALTER TABLE workflow_runs ADD CONSTRAINT ck_workflow_runs_channel
        CHECK (origin_channel IN ('matter_chat','workspace_mcp','automation_service'));
      ALTER TABLE workflow_runs ADD CONSTRAINT ck_workflow_runs_service_rule
        CHECK ((origin_channel='automation_service')=(service_rule_id IS NOT NULL));

      CREATE TABLE automation_service_identities (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
        user_id uuid NOT NULL,
        name varchar(120) NOT NULL,
        capabilities jsonb NOT NULL,
        created_by_user_id uuid NOT NULL,
        status varchar(20) NOT NULL DEFAULT 'active',
        version integer NOT NULL DEFAULT 1,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT uq_automation_service_identity_tenant UNIQUE(tenant_id,id),
        CONSTRAINT uq_automation_service_identity_name UNIQUE(tenant_id,name),
        CONSTRAINT uq_automation_service_identity_user UNIQUE(user_id),
        CONSTRAINT fk_automation_service_identity_user FOREIGN KEY(tenant_id,user_id) REFERENCES users(tenant_id,id) ON DELETE RESTRICT,
        CONSTRAINT fk_automation_service_identity_creator FOREIGN KEY(tenant_id,created_by_user_id) REFERENCES users(tenant_id,id) ON DELETE RESTRICT,
        CONSTRAINT ck_automation_service_identity_status CHECK(status IN ('active','disabled')),
        CONSTRAINT ck_automation_service_identity_grant CHECK(jsonb_typeof(capabilities)='array' AND jsonb_array_length(capabilities) BETWEEN 1 AND 10),
        CONSTRAINT ck_automation_service_identity_version CHECK(version>0)
      );
      CREATE TABLE automation_service_rules (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
        name varchar(120) NOT NULL,
        identity_id uuid NOT NULL,
        matter_id uuid NOT NULL,
        source_run_id uuid NOT NULL,
        schedule jsonb NOT NULL,
        plan_metadata jsonb NOT NULL,
        plan_ciphertext text NOT NULL,
        payload_sha256 varchar(64) NOT NULL,
        plan_sha256 varchar(64) NOT NULL,
        definition_sha256 varchar(64) NOT NULL,
        event_rule_id uuid,
        event_rule_sha256 varchar(64),
        status varchar(20) NOT NULL DEFAULT 'draft',
        created_by_user_id uuid NOT NULL,
        approved_by_user_id uuid,
        approved_at timestamptz,
        version integer NOT NULL DEFAULT 1,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT uq_automation_service_rule_tenant UNIQUE(tenant_id,id),
        CONSTRAINT fk_automation_service_rule_identity FOREIGN KEY(tenant_id,identity_id) REFERENCES automation_service_identities(tenant_id,id) ON DELETE RESTRICT,
        CONSTRAINT fk_automation_service_rule_matter FOREIGN KEY(tenant_id,matter_id) REFERENCES matters(tenant_id,id) ON DELETE RESTRICT,
        CONSTRAINT fk_automation_service_rule_source FOREIGN KEY(tenant_id,source_run_id) REFERENCES workflow_runs(tenant_id,id) ON DELETE RESTRICT,
        CONSTRAINT fk_automation_service_rule_event FOREIGN KEY(tenant_id,event_rule_id) REFERENCES matter_workflow_automation_rules(tenant_id,id) ON DELETE RESTRICT,
        CONSTRAINT fk_automation_service_rule_creator FOREIGN KEY(tenant_id,created_by_user_id) REFERENCES users(tenant_id,id) ON DELETE RESTRICT,
        CONSTRAINT fk_automation_service_rule_approver FOREIGN KEY(tenant_id,approved_by_user_id) REFERENCES users(tenant_id,id) ON DELETE RESTRICT,
        CONSTRAINT ck_automation_service_rule_status CHECK(status IN ('draft','active','paused')),
        CONSTRAINT ck_automation_service_rule_approval CHECK((status='draft')=(approved_by_user_id IS NULL AND approved_at IS NULL)),
        CONSTRAINT ck_automation_service_rule_approval_pair CHECK((approved_by_user_id IS NULL)=(approved_at IS NULL)),
        CONSTRAINT ck_automation_service_rule_hashes CHECK(definition_sha256 ~ '^[a-f0-9]{64}$' AND plan_sha256 ~ '^[a-f0-9]{64}$' AND payload_sha256 ~ '^[a-f0-9]{64}$'),
        CONSTRAINT ck_automation_service_rule_event_pair CHECK((event_rule_id IS NULL)=(event_rule_sha256 IS NULL)),
        CONSTRAINT ck_automation_service_rule_version CHECK(version>0)
      );
      CREATE TABLE automation_service_occurrences (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
        rule_id uuid NOT NULL,
        identity_id uuid NOT NULL,
        occurrence_key varchar(100) NOT NULL,
        source_event_id uuid,
        rule_sha256 varchar(64) NOT NULL,
        run_id uuid,
        outcome varchar(20) NOT NULL,
        failure_code varchar(100),
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT uq_automation_service_occurrence UNIQUE(tenant_id,rule_id,occurrence_key),
        CONSTRAINT uq_automation_service_occurrence_run UNIQUE(run_id),
        CONSTRAINT fk_automation_service_occurrence_rule FOREIGN KEY(tenant_id,rule_id) REFERENCES automation_service_rules(tenant_id,id) ON DELETE RESTRICT,
        CONSTRAINT fk_automation_service_occurrence_identity FOREIGN KEY(tenant_id,identity_id) REFERENCES automation_service_identities(tenant_id,id) ON DELETE RESTRICT,
        CONSTRAINT fk_automation_service_occurrence_run FOREIGN KEY(tenant_id,run_id) REFERENCES workflow_runs(tenant_id,id) ON DELETE RESTRICT,
        CONSTRAINT fk_automation_service_occurrence_event FOREIGN KEY(source_event_id) REFERENCES matter_workflow_automation_events(id) ON DELETE RESTRICT,
        CONSTRAINT ck_automation_service_occurrence_outcome CHECK(outcome IN ('started','blocked','skipped')),
        CONSTRAINT ck_automation_service_occurrence_run CHECK((outcome='started')=(run_id IS NOT NULL)),
        CONSTRAINT ck_automation_service_occurrence_hash CHECK(rule_sha256 ~ '^[a-f0-9]{64}$')
      );
      ALTER TABLE workflow_runs ADD CONSTRAINT fk_workflow_runs_service_rule
        FOREIGN KEY(tenant_id,service_rule_id) REFERENCES automation_service_rules(tenant_id,id) ON DELETE RESTRICT;
      CREATE INDEX ix_automation_service_rules_due ON automation_service_rules(tenant_id,status,created_at);
      CREATE INDEX ix_automation_service_occurrences_budget ON automation_service_occurrences(tenant_id,identity_id,created_at);
    """.split(";\n      "):
        if statement.strip():
            op.execute(statement.strip())
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""CREATE POLICY tenant_isolation ON {table}
          USING (tenant_id=nullif(current_setting('app.current_tenant_id',true),'')::uuid)
          WITH CHECK (tenant_id=nullif(current_setting('app.current_tenant_id',true),'')::uuid)""")
    op.execute("""
      CREATE FUNCTION guard_automation_service_identity() RETURNS trigger
      LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
      BEGIN
        IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Automation service identities are retained'; END IF;
        IF NOT EXISTS (SELECT 1 FROM users u WHERE u.tenant_id=NEW.tenant_id AND u.id=NEW.user_id
          AND u.principal_type='automation_service' AND NOT u.is_active AND NOT u.license_active
          AND NOT u.workspace_mcp_enabled AND u.password_hash IS NULL AND u.oauth_subject IS NULL) THEN
          RAISE EXCEPTION 'Automation identity requires a noninteractive service principal';
        END IF;
        IF TG_OP='UPDATE' AND (NEW.user_id,NEW.name,NEW.capabilities,NEW.created_by_user_id)
          IS DISTINCT FROM (OLD.user_id,OLD.name,OLD.capabilities,OLD.created_by_user_id) THEN
          RAISE EXCEPTION 'Automation identity grant is immutable';
        END IF;
        IF TG_OP='UPDATE' AND NEW.version<>OLD.version+1 THEN RAISE EXCEPTION 'Automation identity version is required'; END IF;
        RETURN NEW;
      END $$;
      CREATE FUNCTION guard_automation_service_user() RETURNS trigger
      LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
      BEGIN
        IF OLD.principal_type='automation_service' AND (NEW.principal_type<>'automation_service'
          OR NEW.is_active OR NEW.license_active OR NEW.workspace_mcp_enabled
          OR NEW.password_hash IS NOT NULL OR NEW.oauth_subject IS NOT NULL) THEN
          RAISE EXCEPTION 'Automation service principals are permanently noninteractive';
        END IF;
        RETURN NEW;
      END $$;
      CREATE FUNCTION guard_automation_service_role() RETURNS trigger
      LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
      BEGIN
        IF EXISTS (SELECT 1 FROM users WHERE id=NEW.user_id AND principal_type='automation_service') THEN
          RAISE EXCEPTION 'Automation service principals cannot receive roles';
        END IF;
        RETURN NEW;
      END $$;
      CREATE FUNCTION guard_automation_service_rule() RETURNS trigger
      LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
      BEGIN
        IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Automation service rules are retained'; END IF;
        IF TG_OP='INSERT' AND NEW.status<>'draft' THEN RAISE EXCEPTION 'Rules begin as drafts'; END IF;
        IF TG_OP='UPDATE' AND (to_jsonb(NEW)-ARRAY['status','version','updated_at','approved_by_user_id','approved_at'])
          IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['status','version','updated_at','approved_by_user_id','approved_at']) THEN
          RAISE EXCEPTION 'Approved service rule definition is immutable';
        END IF;
        IF TG_OP='UPDATE' AND OLD.status='draft' AND NEW.status='active'
          AND (NEW.approved_by_user_id IS NULL OR NEW.approved_at IS NULL) THEN
          RAISE EXCEPTION 'Activating a service rule requires recorded legal approval';
        END IF;
        IF TG_OP='UPDATE' AND NEW.version<>OLD.version+1 THEN RAISE EXCEPTION 'Automation rule version is required'; END IF;
        RETURN NEW;
      END $$;
      CREATE FUNCTION guard_automation_service_occurrence() RETURNS trigger
      LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
      BEGIN
        IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'Automation occurrence evidence is immutable'; END IF;
        IF NEW.outcome='started' AND NOT EXISTS (SELECT 1 FROM workflow_runs r WHERE r.tenant_id=NEW.tenant_id
          AND r.id=NEW.run_id AND r.service_rule_id=NEW.rule_id AND r.actor_user_id=(SELECT user_id FROM automation_service_identities WHERE id=NEW.identity_id)) THEN
          RAISE EXCEPTION 'Service occurrence must bind its exact run and identity';
        END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER automation_service_identities_guard BEFORE INSERT OR UPDATE OR DELETE ON automation_service_identities FOR EACH ROW EXECUTE FUNCTION guard_automation_service_identity();
      CREATE TRIGGER automation_service_rules_guard BEFORE INSERT OR UPDATE OR DELETE ON automation_service_rules FOR EACH ROW EXECUTE FUNCTION guard_automation_service_rule();
      CREATE TRIGGER automation_service_occurrences_guard BEFORE INSERT OR UPDATE OR DELETE ON automation_service_occurrences FOR EACH ROW EXECUTE FUNCTION guard_automation_service_occurrence();
      CREATE TRIGGER users_automation_service_guard BEFORE UPDATE ON users FOR EACH ROW EXECUTE FUNCTION guard_automation_service_user();
      CREATE TRIGGER user_roles_automation_service_guard BEFORE INSERT OR UPDATE ON user_roles FOR EACH ROW EXECUTE FUNCTION guard_automation_service_role();
    """)


def downgrade():
    op.execute("""DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM automation_service_occurrences) OR EXISTS (SELECT 1 FROM automation_service_rules)
        OR EXISTS (SELECT 1 FROM automation_service_identities) THEN
        RAISE EXCEPTION 'Automation service evidence exists; preserve it and roll forward';
      END IF;
    END $$""")
    op.execute(
        "ALTER TABLE workflow_runs DROP CONSTRAINT fk_workflow_runs_service_rule"
    )
    op.execute("DROP TABLE automation_service_occurrences")
    op.execute("DROP TABLE automation_service_rules")
    op.execute("DROP TABLE automation_service_identities")
    op.execute("DROP FUNCTION guard_automation_service_occurrence")
    op.execute("DROP FUNCTION guard_automation_service_rule")
    op.execute("DROP FUNCTION guard_automation_service_identity")
    op.execute("DROP TRIGGER user_roles_automation_service_guard ON user_roles")
    op.execute("DROP TRIGGER users_automation_service_guard ON users")
    op.execute("DROP FUNCTION guard_automation_service_role")
    op.execute("DROP FUNCTION guard_automation_service_user")
    op.execute(
        "ALTER TABLE workflow_runs DROP CONSTRAINT ck_workflow_runs_service_rule"
    )
    op.execute("ALTER TABLE workflow_runs DROP COLUMN service_rule_id")
    op.execute("ALTER TABLE workflow_runs DROP CONSTRAINT ck_workflow_runs_channel")
    op.execute(
        "ALTER TABLE workflow_runs ADD CONSTRAINT ck_workflow_runs_channel CHECK (origin_channel IN ('matter_chat','workspace_mcp'))"
    )
    op.execute("ALTER TABLE users DROP CONSTRAINT ck_users_principal_type")
    op.execute("ALTER TABLE users DROP COLUMN principal_type")
