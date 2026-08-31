"""Tenant configurable fields and approved matter workflow runs."""

from alembic import op


revision = "148_configurable_workflows"
down_revision = "147_studio_drafts"
branch_labels = None
depends_on = None


_UUID = "uuid NOT NULL DEFAULT gen_random_uuid()"
_TENANT = "uuid NOT NULL"


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"""CREATE POLICY {table}_tenant_isolation ON {table}
        USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)"""
    )
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def upgrade() -> None:
    # These targets are required before creating the tenant-safe composite FKs.
    op.execute("""DO $$
    DECLARE contact_columns text[]; task_columns text[];
    BEGIN
      SELECT array_agg(a.attname::text ORDER BY key_columns.ordinality)
        INTO contact_columns
        FROM pg_constraint c
        CROSS JOIN unnest(c.conkey) WITH ORDINALITY AS key_columns(attnum, ordinality)
        JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = key_columns.attnum
       WHERE c.conrelid = 'contacts'::regclass AND c.conname = 'uq_contacts_tenant_id';
      IF contact_columns IS NULL THEN
        ALTER TABLE contacts ADD CONSTRAINT uq_contacts_tenant_id UNIQUE (tenant_id, id);
        COMMENT ON CONSTRAINT uq_contacts_tenant_id ON contacts IS '148_configurable_workflows_created';
      ELSIF contact_columns <> ARRAY['tenant_id', 'id']::text[] THEN
        RAISE EXCEPTION 'contacts.uq_contacts_tenant_id has unexpected columns: %', contact_columns;
      END IF;
      SELECT array_agg(a.attname::text ORDER BY key_columns.ordinality)
        INTO task_columns
        FROM pg_constraint c
        CROSS JOIN unnest(c.conkey) WITH ORDINALITY AS key_columns(attnum, ordinality)
        JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = key_columns.attnum
       WHERE c.conrelid = 'tasks'::regclass AND c.conname = 'uq_tasks_tenant_id';
      IF task_columns IS NULL THEN
        ALTER TABLE tasks ADD CONSTRAINT uq_tasks_tenant_id UNIQUE (tenant_id, id);
        COMMENT ON CONSTRAINT uq_tasks_tenant_id ON tasks IS '148_configurable_workflows_created';
      ELSIF task_columns <> ARRAY['tenant_id', 'id']::text[] THEN
        RAISE EXCEPTION 'tasks.uq_tasks_tenant_id has unexpected columns: %', task_columns;
      END IF;
    END $$""")
    op.execute("""CREATE FUNCTION validate_config_workflow_options(options jsonb)
    RETURNS boolean AS $$
      SELECT CASE WHEN jsonb_typeof(options) = 'array' THEN
          jsonb_array_length(options) <= 100
          AND NOT EXISTS (
            SELECT 1 FROM jsonb_array_elements(options) AS entry(value)
             WHERE jsonb_typeof(entry.value) <> 'string'
                OR btrim(entry.value #>> '{}') = ''
                OR length(entry.value #>> '{}') > 160
          )
          AND (
            SELECT count(*) = count(DISTINCT lower(entry.value #>> '{}'))
              FROM jsonb_array_elements(options) AS entry(value)
          )
        ELSE false END
    $$ LANGUAGE sql IMMUTABLE SET search_path = pg_catalog, public""")
    op.execute(f"""CREATE TABLE custom_field_definitions (
      id {_UUID} PRIMARY KEY, tenant_id {_TENANT}, entity_type varchar(20) NOT NULL,
      field_key varchar(64) NOT NULL, label varchar(160) NOT NULL, description text,
      field_type varchar(30) NOT NULL, options_json jsonb NOT NULL DEFAULT '[]'::jsonb,
      required boolean NOT NULL DEFAULT false, sensitive boolean NOT NULL DEFAULT false,
      active boolean NOT NULL DEFAULT true, schema_version integer NOT NULL DEFAULT 1,
      created_by_user_id uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT uq_custom_field_definitions_tenant_id UNIQUE (tenant_id,id),
      CONSTRAINT uq_custom_field_definitions_tenant_entity UNIQUE (tenant_id,id,entity_type),
      CONSTRAINT uq_custom_field_definitions_key UNIQUE (tenant_id,entity_type,field_key),
      CONSTRAINT ck_custom_field_definitions_entity_type CHECK (entity_type IN ('matter','contact')),
      CONSTRAINT ck_custom_field_definitions_field_type CHECK (field_type IN ('text','long_text','number','date','boolean','single_select','multi_select','contact')),
      CONSTRAINT ck_custom_field_definitions_key CHECK (field_key ~ '^[a-z][a-z0-9_]{{0,63}}$'),
      CONSTRAINT ck_custom_field_definitions_schema_version CHECK (schema_version > 0),
      CONSTRAINT ck_custom_field_definitions_options_shape CHECK (validate_config_workflow_options(options_json)),
      CONSTRAINT ck_custom_field_definitions_options_type CHECK (CASE WHEN jsonb_typeof(options_json)<>'array' THEN false WHEN field_type IN ('single_select','multi_select') THEN jsonb_array_length(options_json) BETWEEN 1 AND 100 ELSE options_json='[]'::jsonb END),
      FOREIGN KEY (tenant_id,created_by_user_id) REFERENCES users(tenant_id,id) ON DELETE RESTRICT
    )""")
    op.execute(
        "CREATE INDEX ix_custom_field_definitions_tenant_scope_active ON custom_field_definitions (tenant_id,entity_type,active)"
    )
    op.execute(f"""CREATE TABLE matter_custom_field_values (
      id {_UUID} PRIMARY KEY, tenant_id {_TENANT}, matter_id uuid NOT NULL, field_definition_id uuid NOT NULL, linked_contact_id uuid,
      entity_type varchar(20) NOT NULL DEFAULT 'matter', value_json jsonb NOT NULL, value_hmac varchar(64) NOT NULL,
      updated_by_user_id uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT uq_matter_custom_field_values_field UNIQUE (tenant_id,matter_id,field_definition_id),
      CONSTRAINT ck_matter_custom_field_values_entity CHECK (entity_type = 'matter'),
      CONSTRAINT ck_matter_custom_field_values_hmac CHECK (value_hmac ~ '^[a-f0-9]{{64}}$'),
      CONSTRAINT ck_matter_custom_field_values_link CHECK (linked_contact_id IS NULL OR value_json=to_jsonb(linked_contact_id::text)),
      FOREIGN KEY (tenant_id,matter_id) REFERENCES matters(tenant_id,id) ON DELETE CASCADE,
      FOREIGN KEY (tenant_id,linked_contact_id) REFERENCES contacts(tenant_id,id) ON DELETE RESTRICT,
      FOREIGN KEY (tenant_id,field_definition_id,entity_type) REFERENCES custom_field_definitions(tenant_id,id,entity_type) ON DELETE RESTRICT,
      FOREIGN KEY (tenant_id,updated_by_user_id) REFERENCES users(tenant_id,id) ON DELETE RESTRICT
    )""")
    op.execute(
        "CREATE INDEX ix_matter_custom_field_values_tenant_matter ON matter_custom_field_values (tenant_id,matter_id)"
    )
    op.execute(f"""CREATE TABLE contact_custom_field_values (
      id {_UUID} PRIMARY KEY, tenant_id {_TENANT}, contact_id uuid NOT NULL, field_definition_id uuid NOT NULL, linked_contact_id uuid,
      entity_type varchar(20) NOT NULL DEFAULT 'contact', value_json jsonb NOT NULL, value_hmac varchar(64) NOT NULL,
      updated_by_user_id uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT uq_contact_custom_field_values_field UNIQUE (tenant_id,contact_id,field_definition_id),
      CONSTRAINT ck_contact_custom_field_values_entity CHECK (entity_type = 'contact'),
      CONSTRAINT ck_contact_custom_field_values_hmac CHECK (value_hmac ~ '^[a-f0-9]{{64}}$'),
      CONSTRAINT ck_contact_custom_field_values_link CHECK (linked_contact_id IS NULL OR value_json=to_jsonb(linked_contact_id::text)),
      FOREIGN KEY (tenant_id,contact_id) REFERENCES contacts(tenant_id,id) ON DELETE CASCADE,
      FOREIGN KEY (tenant_id,linked_contact_id) REFERENCES contacts(tenant_id,id) ON DELETE RESTRICT,
      FOREIGN KEY (tenant_id,field_definition_id,entity_type) REFERENCES custom_field_definitions(tenant_id,id,entity_type) ON DELETE RESTRICT,
      FOREIGN KEY (tenant_id,updated_by_user_id) REFERENCES users(tenant_id,id) ON DELETE RESTRICT
    )""")
    op.execute(
        "CREATE INDEX ix_contact_custom_field_values_tenant_contact ON contact_custom_field_values (tenant_id,contact_id)"
    )
    op.execute("""CREATE FUNCTION prevent_config_field_contract_rewrite() RETURNS trigger AS $$
    BEGIN
      IF OLD.id<>NEW.id OR OLD.tenant_id<>NEW.tenant_id
         OR OLD.entity_type<>NEW.entity_type OR OLD.field_key<>NEW.field_key
         OR OLD.field_type<>NEW.field_type
         OR OLD.created_by_user_id<>NEW.created_by_user_id
         OR OLD.created_at<>NEW.created_at THEN
        RAISE EXCEPTION 'custom field identity and type are immutable';
      END IF;
      IF OLD.sensitive AND NOT NEW.sensitive THEN
        RAISE EXCEPTION 'sensitive custom fields cannot be downgraded';
      END IF;
      IF OLD.options_json<>NEW.options_json AND (
           EXISTS (SELECT 1 FROM public.matter_custom_field_values
                    WHERE tenant_id=OLD.tenant_id AND field_definition_id=OLD.id)
           OR EXISTS (SELECT 1 FROM public.contact_custom_field_values
                       WHERE tenant_id=OLD.tenant_id AND field_definition_id=OLD.id)
         ) THEN
        RAISE EXCEPTION 'custom field options with stored values are immutable';
      END IF;
      IF NEW.schema_version<>OLD.schema_version+1 THEN
        RAISE EXCEPTION 'custom field updates require the next schema version';
      END IF;
      RETURN NEW;
    END; $$ LANGUAGE plpgsql SET search_path = pg_catalog, public""")
    op.execute(
        "CREATE TRIGGER custom_field_definitions_contract BEFORE UPDATE ON custom_field_definitions FOR EACH ROW EXECUTE FUNCTION prevent_config_field_contract_rewrite()"
    )
    op.execute("""CREATE FUNCTION enforce_config_custom_field_value() RETURNS trigger AS $$
    DECLARE definition record; scalar_value text;
    BEGIN
      SELECT field_type, options_json, active INTO definition
        FROM public.custom_field_definitions
       WHERE tenant_id=NEW.tenant_id AND id=NEW.field_definition_id
         AND entity_type=NEW.entity_type;
      IF NOT FOUND THEN
        RETURN NEW;
      END IF;
      IF NOT definition.active THEN
        RAISE EXCEPTION 'inactive custom fields cannot receive values';
      END IF;
      scalar_value := CASE WHEN jsonb_typeof(NEW.value_json)='string'
                           THEN NEW.value_json #>> '{}' ELSE NULL END;
      IF definition.field_type <> 'contact' AND NEW.linked_contact_id IS NOT NULL THEN
        RAISE EXCEPTION 'only contact fields may link a contact';
      ELSIF definition.field_type = 'text' THEN
        IF scalar_value IS NULL OR length(scalar_value)>500 THEN RAISE EXCEPTION 'invalid text custom field value'; END IF;
      ELSIF definition.field_type = 'long_text' THEN
        IF scalar_value IS NULL OR length(scalar_value)>20000 THEN RAISE EXCEPTION 'invalid long text custom field value'; END IF;
      ELSIF definition.field_type = 'number' THEN
        IF scalar_value IS NULL OR scalar_value !~ '^-?(0|[1-9][0-9]*)(\\.[0-9]+)?$' THEN RAISE EXCEPTION 'invalid numeric custom field value'; END IF;
        IF abs(scalar_value::numeric)>1000000000000000 THEN RAISE EXCEPTION 'invalid numeric custom field value'; END IF;
      ELSIF definition.field_type = 'date' THEN
        IF scalar_value IS NULL OR scalar_value !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' THEN RAISE EXCEPTION 'invalid date custom field value'; END IF;
        IF to_char(scalar_value::date, 'YYYY-MM-DD')<>scalar_value THEN RAISE EXCEPTION 'invalid date custom field value'; END IF;
      ELSIF definition.field_type = 'boolean' THEN
        IF jsonb_typeof(NEW.value_json)<>'boolean' THEN RAISE EXCEPTION 'invalid boolean custom field value'; END IF;
      ELSIF definition.field_type = 'single_select' THEN
        IF scalar_value IS NULL OR NOT (definition.options_json ? scalar_value) THEN RAISE EXCEPTION 'invalid select custom field value'; END IF;
      ELSIF definition.field_type = 'multi_select' THEN
        IF jsonb_typeof(NEW.value_json)<>'array' THEN RAISE EXCEPTION 'invalid multi-select custom field value'; END IF;
        IF jsonb_array_length(NEW.value_json)>100
           OR EXISTS (SELECT 1 FROM jsonb_array_elements(NEW.value_json) AS entry(value)
                       WHERE jsonb_typeof(entry.value)<>'string'
                          OR NOT (definition.options_json ? (entry.value #>> '{}')))
           OR (SELECT count(*)<>count(DISTINCT entry.value #>> '{}')
                 FROM jsonb_array_elements(NEW.value_json) AS entry(value))
        THEN RAISE EXCEPTION 'invalid multi-select custom field value'; END IF;
      ELSIF definition.field_type = 'contact' THEN
        IF scalar_value IS NULL OR NEW.linked_contact_id IS NULL
           OR scalar_value<>NEW.linked_contact_id::text THEN RAISE EXCEPTION 'invalid contact custom field value'; END IF;
      ELSE
        RAISE EXCEPTION 'unsupported custom field type';
      END IF;
      RETURN NEW;
    EXCEPTION WHEN invalid_text_representation OR datetime_field_overflow OR numeric_value_out_of_range THEN
      RAISE EXCEPTION 'invalid typed custom field value';
    END; $$ LANGUAGE plpgsql SET search_path = pg_catalog, public""")
    for table in ("matter_custom_field_values", "contact_custom_field_values"):
        op.execute(
            f"CREATE TRIGGER {table}_validate BEFORE INSERT OR UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION enforce_config_custom_field_value()"
        )
    op.execute(f"""CREATE TABLE matter_workflow_templates (
      id {_UUID} PRIMARY KEY, tenant_id {_TENANT}, name varchar(200) NOT NULL, description text,
      active boolean NOT NULL DEFAULT true, created_by_user_id uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT uq_matter_workflow_templates_tenant_id UNIQUE (tenant_id,id), CONSTRAINT uq_matter_workflow_templates_name UNIQUE (tenant_id,name),
      CONSTRAINT ck_matter_workflow_templates_name CHECK (btrim(name) <> ''), FOREIGN KEY (tenant_id,created_by_user_id) REFERENCES users(tenant_id,id) ON DELETE RESTRICT
    )""")
    op.execute(
        "CREATE INDEX ix_matter_workflow_templates_tenant_active ON matter_workflow_templates (tenant_id,active)"
    )
    op.execute(f"""CREATE TABLE matter_workflow_template_versions (
      id {_UUID} PRIMARY KEY, tenant_id {_TENANT}, template_id uuid NOT NULL, version integer NOT NULL,
      status varchar(20) NOT NULL DEFAULT 'draft', initial_stage_key varchar(64) NOT NULL, definition_sha256 varchar(64) NOT NULL,
      created_by_user_id uuid NOT NULL, approved_by_user_id uuid, approved_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT uq_matter_workflow_template_versions_tenant_id UNIQUE (tenant_id,id), CONSTRAINT uq_matter_workflow_template_versions_number UNIQUE (tenant_id,template_id,version),
      CONSTRAINT ck_matter_workflow_template_versions_status CHECK (status IN ('draft','approved')), CONSTRAINT ck_matter_workflow_template_versions_version CHECK (version > 0),
      CONSTRAINT ck_matter_workflow_template_versions_initial_stage CHECK (initial_stage_key ~ '^[a-z][a-z0-9_]{{0,63}}$'),
      CONSTRAINT ck_matter_workflow_template_versions_hash CHECK (definition_sha256 ~ '^[a-f0-9]{{64}}$'),
      CONSTRAINT ck_matter_workflow_template_versions_approval CHECK ((status='approved') = (approved_by_user_id IS NOT NULL AND approved_at IS NOT NULL)),
      FOREIGN KEY (tenant_id,template_id) REFERENCES matter_workflow_templates(tenant_id,id) ON DELETE RESTRICT,
      FOREIGN KEY (tenant_id,created_by_user_id) REFERENCES users(tenant_id,id) ON DELETE RESTRICT,
      FOREIGN KEY (tenant_id,approved_by_user_id) REFERENCES users(tenant_id,id) ON DELETE RESTRICT
    )""")
    op.execute(
        "CREATE INDEX ix_matter_workflow_template_versions_tenant_status ON matter_workflow_template_versions (tenant_id,status)"
    )
    op.execute(f"""CREATE TABLE matter_workflow_stage_definitions (
      id {_UUID} PRIMARY KEY, tenant_id {_TENANT}, template_version_id uuid NOT NULL, stage_key varchar(64) NOT NULL, label varchar(160) NOT NULL, position integer NOT NULL,
      CONSTRAINT uq_matter_workflow_stage_definitions_key UNIQUE (tenant_id,template_version_id,stage_key), CONSTRAINT uq_matter_workflow_stage_definitions_position UNIQUE (tenant_id,template_version_id,position),
      CONSTRAINT ck_matter_workflow_stage_definitions_key CHECK (stage_key ~ '^[a-z][a-z0-9_]{{0,63}}$'), CONSTRAINT ck_matter_workflow_stage_definitions_label CHECK (btrim(label)<>''), CONSTRAINT ck_matter_workflow_stage_definitions_position CHECK (position BETWEEN 0 AND 49),
      FOREIGN KEY (tenant_id,template_version_id) REFERENCES matter_workflow_template_versions(tenant_id,id) ON DELETE RESTRICT
    )""")
    op.execute(f"""CREATE TABLE matter_workflow_checklist_definitions (
      id {_UUID} PRIMARY KEY, tenant_id {_TENANT}, template_version_id uuid NOT NULL, stage_key varchar(64) NOT NULL, item_key varchar(64) NOT NULL, title varchar(500) NOT NULL, description text,
      position integer NOT NULL, task_type varchar(50) NOT NULL, priority varchar(20) NOT NULL, due_offset_days integer NOT NULL, assignee_role varchar(30) NOT NULL,
      CONSTRAINT uq_matter_workflow_checklist_definitions_key UNIQUE (tenant_id,template_version_id,item_key), CONSTRAINT uq_matter_workflow_checklist_definitions_position UNIQUE (tenant_id,template_version_id,position),
      CONSTRAINT ck_matter_workflow_checklist_definitions_key CHECK (item_key ~ '^[a-z][a-z0-9_]{{0,63}}$'), CONSTRAINT ck_matter_workflow_checklist_definitions_title CHECK (btrim(title)<>''), CONSTRAINT ck_matter_workflow_checklist_definitions_position CHECK (position BETWEEN 0 AND 199),
      CONSTRAINT ck_matter_workflow_checklist_definitions_offset CHECK (due_offset_days BETWEEN 0 AND 3650), CONSTRAINT ck_matter_workflow_checklist_definitions_task_type CHECK (task_type IN ('deadline','hearing','filing','deposition','call','follow_up','intake','review','general')),
      CONSTRAINT ck_matter_workflow_checklist_definitions_priority CHECK (priority IN ('low','medium','high','urgent')), CONSTRAINT ck_matter_workflow_checklist_definitions_assignee CHECK (assignee_role IN ('matter_owner','attorney_of_record','template_applier','unassigned')),
      FOREIGN KEY (tenant_id,template_version_id,stage_key) REFERENCES matter_workflow_stage_definitions(tenant_id,template_version_id,stage_key) ON DELETE RESTRICT
    )""")
    op.execute(f"""CREATE TABLE matter_workflow_field_requirements (
      id {_UUID} PRIMARY KEY, tenant_id {_TENANT}, template_version_id uuid NOT NULL, field_definition_id uuid NOT NULL, entity_type varchar(20) NOT NULL DEFAULT 'matter',
      CONSTRAINT uq_matter_workflow_field_requirements_field UNIQUE (tenant_id,template_version_id,field_definition_id), CONSTRAINT ck_matter_workflow_field_requirements_entity CHECK (entity_type='matter'),
      FOREIGN KEY (tenant_id,template_version_id) REFERENCES matter_workflow_template_versions(tenant_id,id) ON DELETE RESTRICT,
      FOREIGN KEY (tenant_id,field_definition_id,entity_type) REFERENCES custom_field_definitions(tenant_id,id,entity_type) ON DELETE RESTRICT
    )""")
    op.execute(f"""CREATE TABLE matter_workflow_runs (
      id {_UUID} PRIMARY KEY, tenant_id {_TENANT}, matter_id uuid NOT NULL, template_version_id uuid NOT NULL, idempotency_key varchar(200) NOT NULL, request_sha256 varchar(64) NOT NULL, template_sha256 varchar(64) NOT NULL, matter_sha256 varchar(64) NOT NULL, preview_sha256 varchar(64) NOT NULL, preview_json jsonb NOT NULL,
      status varchar(30) NOT NULL DEFAULT 'planned', prior_stage varchar(200), planned_by_user_id uuid NOT NULL, approved_by_user_id uuid, approved_at timestamptz, rolled_back_by_user_id uuid, rolled_back_at timestamptz, rollback_idempotency_key varchar(200), rollback_request_sha256 varchar(64), failure_code varchar(80), failure_detail text, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT uq_matter_workflow_runs_tenant_id UNIQUE (tenant_id,id), CONSTRAINT uq_matter_workflow_runs_idempotency UNIQUE (tenant_id,matter_id,idempotency_key), CONSTRAINT ck_matter_workflow_runs_status CHECK (status IN ('planned','applied','failed','compensation_required','rolled_back')), CONSTRAINT ck_matter_workflow_runs_approval CHECK ((status IN ('applied','compensation_required','rolled_back')) = (approved_by_user_id IS NOT NULL AND approved_at IS NOT NULL)), CONSTRAINT ck_matter_workflow_runs_hashes CHECK (request_sha256 ~ '^[a-f0-9]{{64}}$' AND template_sha256 ~ '^[a-f0-9]{{64}}$' AND matter_sha256 ~ '^[a-f0-9]{{64}}$' AND preview_sha256 ~ '^[a-f0-9]{{64}}$'),
      FOREIGN KEY (tenant_id,matter_id) REFERENCES matters(tenant_id,id) ON DELETE RESTRICT, FOREIGN KEY (tenant_id,template_version_id) REFERENCES matter_workflow_template_versions(tenant_id,id) ON DELETE RESTRICT, FOREIGN KEY (tenant_id,planned_by_user_id) REFERENCES users(tenant_id,id) ON DELETE RESTRICT, FOREIGN KEY (tenant_id,approved_by_user_id) REFERENCES users(tenant_id,id) ON DELETE RESTRICT, FOREIGN KEY (tenant_id,rolled_back_by_user_id) REFERENCES users(tenant_id,id) ON DELETE RESTRICT
    )""")
    op.execute(
        "CREATE INDEX ix_matter_workflow_runs_tenant_matter_created ON matter_workflow_runs (tenant_id,matter_id,created_at)"
    )
    op.execute(f"""CREATE TABLE matter_workflow_run_events (
      id {_UUID} PRIMARY KEY, tenant_id {_TENANT}, run_id uuid NOT NULL, sequence integer NOT NULL, event_type varchar(40) NOT NULL, actor_user_id uuid NOT NULL, detail_json jsonb NOT NULL DEFAULT '{{}}'::jsonb, evidence_sha256 varchar(64) NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT uq_matter_workflow_run_events_sequence UNIQUE (tenant_id,run_id,sequence), CONSTRAINT ck_matter_workflow_run_events_sequence CHECK (sequence>0), CONSTRAINT ck_matter_workflow_run_events_type CHECK (event_type IN ('previewed','approved','applied','failed','rollback_requested','rollback_blocked','rolled_back')), CONSTRAINT ck_matter_workflow_run_events_hash CHECK (evidence_sha256 ~ '^[a-f0-9]{{64}}$'), FOREIGN KEY (tenant_id,run_id) REFERENCES matter_workflow_runs(tenant_id,id) ON DELETE RESTRICT, FOREIGN KEY (tenant_id,actor_user_id) REFERENCES users(tenant_id,id) ON DELETE RESTRICT
    )""")
    op.execute(
        "CREATE INDEX ix_matter_workflow_run_events_tenant_run_created ON matter_workflow_run_events (tenant_id,run_id,created_at)"
    )
    op.execute(f"""CREATE TABLE matter_workflow_run_steps (
      id {_UUID} PRIMARY KEY, tenant_id {_TENANT}, run_id uuid NOT NULL, sequence integer NOT NULL, step_type varchar(30) NOT NULL, action_key varchar(100) NOT NULL, status varchar(20) NOT NULL, task_id uuid, evidence_json jsonb NOT NULL DEFAULT '{{}}'::jsonb, evidence_sha256 varchar(64) NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT uq_matter_workflow_run_steps_sequence UNIQUE (tenant_id,run_id,sequence), CONSTRAINT ck_matter_workflow_run_steps_sequence CHECK (sequence>0), CONSTRAINT ck_matter_workflow_run_steps_type CHECK (step_type IN ('matter_stage','task_create','task_cancel','stage_restore')), CONSTRAINT ck_matter_workflow_run_steps_status CHECK (status IN ('succeeded','blocked')), CONSTRAINT ck_matter_workflow_run_steps_hash CHECK (evidence_sha256 ~ '^[a-f0-9]{{64}}$'), FOREIGN KEY (tenant_id,run_id) REFERENCES matter_workflow_runs(tenant_id,id) ON DELETE RESTRICT, FOREIGN KEY (tenant_id,task_id) REFERENCES tasks(tenant_id,id) ON DELETE RESTRICT
    )""")
    op.execute(
        "CREATE INDEX ix_matter_workflow_run_steps_tenant_run_sequence ON matter_workflow_run_steps (tenant_id,run_id,sequence)"
    )
    tables = (
        "custom_field_definitions",
        "matter_custom_field_values",
        "contact_custom_field_values",
        "matter_workflow_templates",
        "matter_workflow_template_versions",
        "matter_workflow_stage_definitions",
        "matter_workflow_checklist_definitions",
        "matter_workflow_field_requirements",
        "matter_workflow_runs",
        "matter_workflow_run_events",
        "matter_workflow_run_steps",
    )
    for table in tables:
        _rls(table)
    op.execute(
        """CREATE FUNCTION config_workflow_demo_purge_authorized(row_tenant uuid) RETURNS boolean AS $$
        SELECT current_setting('app.config_workflow_demo_purge_tenant_id', true) = row_tenant::text
          AND EXISTS (
            SELECT 1
            FROM public.tenants tenant
            JOIN public.demo_sessions demo ON demo.tenant_id = tenant.id
            WHERE tenant.id = row_tenant
              AND tenant.billing_tier = 'demo'
              AND tenant.domain LIKE '%.demo.invalid'
              AND tenant.is_active = false
              AND tenant.expires_at <= now()
              AND demo.id::text = current_setting('app.config_workflow_demo_purge_session_id', true)
              AND demo.status = 'purging'
              AND demo.expires_at <= now()
              AND demo.fixture_tenant_id <> demo.tenant_id
              AND demo.purge_started_at IS NOT NULL
          );
        $$ LANGUAGE sql STABLE SET search_path = pg_catalog, public"""
    )
    op.execute(
        """CREATE FUNCTION prevent_config_workflow_immutable() RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'DELETE' AND public.config_workflow_demo_purge_authorized(OLD.tenant_id)
          THEN RETURN OLD; END IF;
          RAISE EXCEPTION 'configurable workflow history is immutable';
        END; $$ LANGUAGE plpgsql SET search_path = pg_catalog, public"""
    )
    for table in ("matter_workflow_run_events", "matter_workflow_run_steps"):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION prevent_config_workflow_immutable()"
        )
    op.execute("""CREATE FUNCTION prevent_config_workflow_run_tamper() RETURNS trigger AS $$
    BEGIN
      IF TG_OP = 'DELETE' AND public.config_workflow_demo_purge_authorized(OLD.tenant_id)
      THEN RETURN OLD; END IF;
      IF TG_OP='DELETE' OR OLD.tenant_id<>NEW.tenant_id OR OLD.matter_id<>NEW.matter_id
         OR OLD.template_version_id<>NEW.template_version_id
         OR OLD.idempotency_key<>NEW.idempotency_key
         OR OLD.request_sha256<>NEW.request_sha256
         OR OLD.template_sha256<>NEW.template_sha256
         OR OLD.matter_sha256<>NEW.matter_sha256
         OR OLD.preview_sha256<>NEW.preview_sha256
         OR OLD.preview_json<>NEW.preview_json
         OR OLD.prior_stage IS DISTINCT FROM NEW.prior_stage
         OR OLD.planned_by_user_id<>NEW.planned_by_user_id
         OR OLD.created_at<>NEW.created_at THEN
        RAISE EXCEPTION 'workflow run planning evidence is immutable';
      END IF;
      RETURN NEW;
    END; $$ LANGUAGE plpgsql SET search_path = pg_catalog, public""")
    op.execute(
        "CREATE TRIGGER matter_workflow_runs_snapshot_immutable BEFORE UPDATE OR DELETE ON matter_workflow_runs FOR EACH ROW EXECUTE FUNCTION prevent_config_workflow_run_tamper()"
    )
    op.execute("""CREATE FUNCTION prevent_approved_workflow_mutation() RETURNS trigger AS $$
    DECLARE version_status text; new_version_status text;
    BEGIN
      IF TG_TABLE_NAME = 'matter_workflow_template_versions' THEN
        IF TG_OP = 'INSERT' THEN
          IF NEW.status <> 'draft' OR NEW.approved_by_user_id IS NOT NULL OR NEW.approved_at IS NOT NULL THEN
            RAISE EXCEPTION 'workflow template versions must be created as drafts';
          END IF;
          RETURN NEW;
        END IF;
        IF TG_OP = 'DELETE' THEN
          IF public.config_workflow_demo_purge_authorized(OLD.tenant_id) THEN
            RETURN OLD;
          END IF;
          IF OLD.status = 'approved' THEN
            RAISE EXCEPTION 'approved workflow template definitions are immutable';
          END IF;
          RETURN OLD;
        END IF;
        IF OLD.status = 'draft' AND NEW.status = 'approved'
           AND OLD.approved_by_user_id IS NULL AND OLD.approved_at IS NULL
           AND NEW.approved_by_user_id IS NOT NULL AND NEW.approved_at IS NOT NULL
           AND OLD.id=NEW.id AND OLD.tenant_id=NEW.tenant_id
           AND OLD.template_id=NEW.template_id AND OLD.version=NEW.version
           AND OLD.initial_stage_key=NEW.initial_stage_key
           AND OLD.definition_sha256=NEW.definition_sha256
           AND OLD.created_by_user_id=NEW.created_by_user_id
           AND OLD.created_at=NEW.created_at THEN
          RETURN NEW;
        END IF;
        IF OLD.status = 'approved' THEN
          RAISE EXCEPTION 'approved workflow template definitions are immutable';
        END IF;
        IF NEW.status <> 'draft'
           OR NEW.approved_by_user_id IS NOT NULL
           OR NEW.approved_at IS NOT NULL THEN
          RAISE EXCEPTION 'workflow template approval transition must be exact';
        END IF;
        RETURN NEW;
      END IF;

      -- Child tables do not have version-only fields such as status. Branch
      -- by operation before dereferencing OLD/NEW so PostgreSQL never plans a
      -- table-incompatible record-field access.
      IF TG_OP = 'INSERT' THEN
        SELECT status INTO version_status
          FROM public.matter_workflow_template_versions
         WHERE tenant_id=NEW.tenant_id AND id=NEW.template_version_id
         FOR SHARE;
        IF version_status = 'approved' THEN
          RAISE EXCEPTION 'approved workflow template definitions are immutable';
        END IF;
        RETURN NEW;
      END IF;
      IF TG_OP = 'DELETE' THEN
        IF public.config_workflow_demo_purge_authorized(OLD.tenant_id) THEN
          RETURN OLD;
        END IF;
        SELECT status INTO version_status
          FROM public.matter_workflow_template_versions
         WHERE tenant_id=OLD.tenant_id AND id=OLD.template_version_id
         FOR SHARE;
        IF version_status = 'approved' THEN
          RAISE EXCEPTION 'approved workflow template definitions are immutable';
        END IF;
        RETURN OLD;
      END IF;
      SELECT status INTO version_status
        FROM public.matter_workflow_template_versions
       WHERE tenant_id=OLD.tenant_id AND id=OLD.template_version_id
       FOR SHARE;
      IF OLD.tenant_id = NEW.tenant_id
         AND OLD.template_version_id = NEW.template_version_id THEN
        new_version_status := version_status;
      ELSE
        SELECT status INTO new_version_status
          FROM public.matter_workflow_template_versions
         WHERE tenant_id=NEW.tenant_id AND id=NEW.template_version_id
         FOR SHARE;
      END IF;
      IF version_status = 'approved' OR new_version_status = 'approved' THEN
        RAISE EXCEPTION 'approved workflow template definitions are immutable';
      END IF;
      RETURN NEW;
    END; $$ LANGUAGE plpgsql SET search_path = pg_catalog, public""")
    for table in (
        "matter_workflow_template_versions",
        "matter_workflow_stage_definitions",
        "matter_workflow_checklist_definitions",
        "matter_workflow_field_requirements",
    ):
        op.execute(
            f"CREATE TRIGGER {table}_approved_immutable BEFORE INSERT OR UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION prevent_approved_workflow_mutation()"
        )
    # Existing system Administrator roles were seeded from the capability
    # catalog at tenant creation time. Keep those tenants operable after this
    # additive capability ships; custom roles remain an explicit firm choice.
    op.execute(
        "UPDATE roles SET capabilities = capabilities || "
        "'[\"manage_workflows\"]'::jsonb, updated_at = now() "
        "WHERE name = 'Administrator' AND is_system IS TRUE "
        "AND NOT (capabilities @> '[\"manage_workflows\"]'::jsonb)"
    )


def downgrade() -> None:
    # Deliberately retain manage_workflows on existing Administrator roles.
    # The upgrade only appends the capability when absent and stores no
    # provenance that can distinguish its grant from a pre-existing firm
    # choice; removing it here could silently revoke an intentional grant.
    op.execute(
        "DROP TRIGGER IF EXISTS matter_workflow_runs_snapshot_immutable ON matter_workflow_runs"
    )
    for table in ("matter_custom_field_values", "contact_custom_field_values"):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_validate ON {table}")
    op.execute(
        "DROP TRIGGER IF EXISTS custom_field_definitions_contract ON custom_field_definitions"
    )
    for table in (
        "matter_workflow_template_versions",
        "matter_workflow_stage_definitions",
        "matter_workflow_checklist_definitions",
        "matter_workflow_field_requirements",
        "matter_workflow_run_events",
        "matter_workflow_run_steps",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_approved_immutable ON {table}")
        op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable ON {table}")
    op.execute("DROP FUNCTION IF EXISTS prevent_approved_workflow_mutation()")
    op.execute("DROP FUNCTION IF EXISTS prevent_config_workflow_run_tamper()")
    op.execute("DROP FUNCTION IF EXISTS prevent_config_workflow_immutable()")
    op.execute("DROP FUNCTION IF EXISTS enforce_config_custom_field_value()")
    op.execute("DROP FUNCTION IF EXISTS prevent_config_field_contract_rewrite()")
    op.execute("DROP FUNCTION IF EXISTS config_workflow_demo_purge_authorized(uuid)")
    for table in (
        "matter_workflow_run_steps",
        "matter_workflow_run_events",
        "matter_workflow_runs",
        "matter_workflow_field_requirements",
        "matter_workflow_checklist_definitions",
        "matter_workflow_stage_definitions",
        "matter_workflow_template_versions",
        "matter_workflow_templates",
        "contact_custom_field_values",
        "matter_custom_field_values",
        "custom_field_definitions",
    ):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.drop_table(table)
    op.execute("DROP FUNCTION IF EXISTS validate_config_workflow_options(jsonb)")
    op.execute("""DO $$ DECLARE r record; BEGIN
      FOR r IN SELECT conrelid::regclass AS rel, conname FROM pg_constraint
        WHERE conname IN ('uq_contacts_tenant_id','uq_tasks_tenant_id')
          AND obj_description(oid, 'pg_constraint') = '148_configurable_workflows_created'
      LOOP EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I', r.rel, r.conname); END LOOP;
    END $$""")
