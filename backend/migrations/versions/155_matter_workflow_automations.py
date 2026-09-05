"""Approval-gated automation rules that plan bounded matter workflow runs.

COMP-09 shipped templates a person had to remember to run. This adds the
trigger half: a firm-defined rule that watches one bounded matter lifecycle
event and, when it matches, plans the same reviewable workflow run the manual
preview endpoint plans.

A rule never applies anything. It produces a ``planned`` row in
``matter_workflow_runs`` and one immutable dispatch record, and the existing
``approve_legal_work`` apply path stays the only way a task or stage changes.

Definition columns cannot change while a rule is active: an edit has to move
the rule back to ``draft`` in the same statement, which forces a second
approval before it can fire again.
"""

from alembic import op


revision = "155_matter_workflow_automations"
down_revision = "154_matter_document_folders"
branch_labels = None
depends_on = None


_UUID = "uuid NOT NULL DEFAULT gen_random_uuid()"
_TENANT = "uuid NOT NULL"
_TABLES = (
    "matter_workflow_automation_rules",
    "matter_workflow_automation_events",
)


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"""CREATE POLICY {table}_tenant_isolation ON {table}
        USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)"""
    )
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def upgrade() -> None:
    op.execute(f"""CREATE TABLE matter_workflow_automation_rules (
      id {_UUID} PRIMARY KEY, tenant_id {_TENANT}, name varchar(120) NOT NULL,
      trigger_event varchar(40) NOT NULL, trigger_stage varchar(200),
      match_matter_type varchar(100), match_practice_area varchar(200),
      template_id uuid NOT NULL, status varchar(20) NOT NULL DEFAULT 'draft',
      definition_sha256 varchar(64) NOT NULL, created_by_user_id uuid NOT NULL,
      activated_by_user_id uuid, activated_at timestamptz,
      archived_at timestamptz,
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT uq_matter_workflow_automation_rules_tenant_id UNIQUE (tenant_id,id),
      CONSTRAINT ck_matter_workflow_automation_rules_event CHECK (trigger_event IN ('matter_created','matter_stage_changed')),
      CONSTRAINT ck_matter_workflow_automation_rules_stage CHECK ((trigger_event = 'matter_stage_changed') = (trigger_stage IS NOT NULL)),
      CONSTRAINT ck_matter_workflow_automation_rules_status CHECK (status IN ('draft','active','archived')),
      CONSTRAINT ck_matter_workflow_automation_rules_activation CHECK ((status = 'active') = (activated_by_user_id IS NOT NULL AND activated_at IS NOT NULL)),
      CONSTRAINT ck_matter_workflow_automation_rules_archival CHECK ((status = 'archived') = (archived_at IS NOT NULL)),
      CONSTRAINT ck_matter_workflow_automation_rules_hash CHECK (definition_sha256 ~ '^[a-f0-9]{{64}}$'),
      CONSTRAINT ck_matter_workflow_automation_rules_name CHECK (char_length(btrim(name)) > 0),
      CONSTRAINT ck_matter_workflow_automation_rules_trimmed CHECK (
        (trigger_stage IS NULL OR char_length(btrim(trigger_stage)) > 0)
        AND (match_matter_type IS NULL OR char_length(btrim(match_matter_type)) > 0)
        AND (match_practice_area IS NULL OR char_length(btrim(match_practice_area)) > 0)),
      FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
      FOREIGN KEY (tenant_id,template_id) REFERENCES matter_workflow_templates(tenant_id,id) ON DELETE RESTRICT,
      FOREIGN KEY (tenant_id,created_by_user_id) REFERENCES users(tenant_id,id) ON DELETE RESTRICT,
      FOREIGN KEY (tenant_id,activated_by_user_id) REFERENCES users(tenant_id,id) ON DELETE RESTRICT
    )""")
    # A firm may reuse a retired rule's name, so uniqueness covers only the
    # rules it can still reach.
    op.execute(
        """CREATE UNIQUE INDEX uq_matter_workflow_automation_rules_name
        ON matter_workflow_automation_rules (tenant_id, lower(btrim(name)))
        WHERE status <> 'archived'"""
    )
    # Two identical active rules would plan two runs from one matter event.
    # NULL never equals NULL in a unique index, so fold the optional
    # conditions onto a sentinel that cannot be a real matter value.
    op.execute(
        """CREATE UNIQUE INDEX uq_matter_workflow_automation_rules_active_trigger
        ON matter_workflow_automation_rules (
            tenant_id,
            trigger_event,
            coalesce(lower(btrim(trigger_stage)), ''),
            coalesce(lower(btrim(match_matter_type)), ''),
            coalesce(lower(btrim(match_practice_area)), ''),
            template_id
        )
        WHERE status = 'active'"""
    )
    op.execute(
        """CREATE INDEX ix_matter_workflow_automation_rules_dispatch
        ON matter_workflow_automation_rules (tenant_id, trigger_event, status)"""
    )

    op.execute(f"""CREATE TABLE matter_workflow_automation_events (
      id {_UUID} PRIMARY KEY, tenant_id {_TENANT}, rule_id uuid NOT NULL,
      matter_id uuid NOT NULL, trigger_event varchar(40) NOT NULL,
      dedupe_key varchar(64) NOT NULL, outcome varchar(20) NOT NULL,
      run_id uuid, rule_sha256 varchar(64) NOT NULL,
      actor_user_id uuid NOT NULL,
      detail_json jsonb NOT NULL DEFAULT '{{}}'::jsonb,
      evidence_sha256 varchar(64) NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT uq_matter_workflow_automation_events_dedupe UNIQUE (tenant_id,rule_id,dedupe_key),
      CONSTRAINT ck_matter_workflow_automation_events_outcome CHECK (outcome IN ('planned','blocked')),
      CONSTRAINT ck_matter_workflow_automation_events_run CHECK ((outcome = 'planned') = (run_id IS NOT NULL)),
      CONSTRAINT ck_matter_workflow_automation_events_event CHECK (trigger_event IN ('matter_created','matter_stage_changed')),
      CONSTRAINT ck_matter_workflow_automation_events_hashes CHECK (dedupe_key ~ '^[a-f0-9]{{64}}$' AND rule_sha256 ~ '^[a-f0-9]{{64}}$' AND evidence_sha256 ~ '^[a-f0-9]{{64}}$'),
      FOREIGN KEY (tenant_id,rule_id) REFERENCES matter_workflow_automation_rules(tenant_id,id) ON DELETE RESTRICT,
      FOREIGN KEY (tenant_id,matter_id) REFERENCES matters(tenant_id,id) ON DELETE RESTRICT,
      FOREIGN KEY (tenant_id,run_id) REFERENCES matter_workflow_runs(tenant_id,id) ON DELETE RESTRICT,
      FOREIGN KEY (tenant_id,actor_user_id) REFERENCES users(tenant_id,id) ON DELETE RESTRICT
    )""")
    op.execute(
        """CREATE INDEX ix_matter_workflow_automation_events_matter
        ON matter_workflow_automation_events (tenant_id, matter_id, created_at)"""
    )
    op.execute(
        """CREATE INDEX ix_matter_workflow_automation_events_rule
        ON matter_workflow_automation_events (tenant_id, rule_id, created_at)"""
    )

    for table in _TABLES:
        _rls(table)

    # Dispatch evidence reuses the COMP-09 append-only trigger, so an expired
    # demo purge stays the only authorized delete path.
    op.execute(
        "CREATE TRIGGER matter_workflow_automation_events_immutable"
        " BEFORE UPDATE OR DELETE ON matter_workflow_automation_events"
        " FOR EACH ROW EXECUTE FUNCTION prevent_config_workflow_immutable()"
    )
    op.execute("""CREATE FUNCTION prevent_workflow_automation_rule_tamper() RETURNS trigger AS $$
    DECLARE definition_changed boolean;
    BEGIN
      IF TG_OP = 'DELETE' THEN
        IF public.config_workflow_demo_purge_authorized(OLD.tenant_id) THEN
          RETURN OLD;
        END IF;
        RAISE EXCEPTION 'workflow automation rules are archived, never deleted';
      END IF;
      IF OLD.tenant_id <> NEW.tenant_id OR OLD.id <> NEW.id
         OR OLD.created_by_user_id <> NEW.created_by_user_id
         OR OLD.created_at <> NEW.created_at THEN
        RAISE EXCEPTION 'workflow automation rule identity is immutable';
      END IF;
      IF OLD.status = 'archived' AND NEW.status <> 'archived' THEN
        RAISE EXCEPTION 'archived workflow automation rules cannot be reopened';
      END IF;
      definition_changed :=
        OLD.trigger_event <> NEW.trigger_event
        OR OLD.trigger_stage IS DISTINCT FROM NEW.trigger_stage
        OR OLD.match_matter_type IS DISTINCT FROM NEW.match_matter_type
        OR OLD.match_practice_area IS DISTINCT FROM NEW.match_practice_area
        OR OLD.template_id <> NEW.template_id
        OR OLD.definition_sha256 <> NEW.definition_sha256;
      IF definition_changed AND NEW.status <> 'draft' THEN
        RAISE EXCEPTION 'an edited workflow automation rule must return to draft';
      END IF;
      IF OLD.status <> 'active' AND NEW.status = 'active' AND definition_changed THEN
        RAISE EXCEPTION 'workflow automation approval must not change the rule';
      END IF;
      RETURN NEW;
    END; $$ LANGUAGE plpgsql SET search_path = pg_catalog, public""")
    op.execute(
        "CREATE TRIGGER matter_workflow_automation_rules_tamper"
        " BEFORE UPDATE OR DELETE ON matter_workflow_automation_rules"
        " FOR EACH ROW EXECUTE FUNCTION prevent_workflow_automation_rule_tamper()"
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS matter_workflow_automation_rules_tamper"
        " ON matter_workflow_automation_rules"
    )
    op.execute("DROP FUNCTION IF EXISTS prevent_workflow_automation_rule_tamper()")
    op.execute(
        "DROP TRIGGER IF EXISTS matter_workflow_automation_events_immutable"
        " ON matter_workflow_automation_events"
    )
    op.execute("DROP TABLE IF EXISTS matter_workflow_automation_events")
    op.execute("DROP TABLE IF EXISTS matter_workflow_automation_rules")
