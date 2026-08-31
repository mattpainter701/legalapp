import app.models  # noqa: F401 -- registers every model table with Base.metadata
from app.database import Base
from app.services.demo_registry import DEMO_TABLE_REGISTRY, SENSITIVE_NEVER_CLONE


def test_every_tenant_scoped_table_has_an_explicit_purge_policy():
    tenant_tables = {
        table.name for table in Base.metadata.tables.values() if "tenant_id" in table.c
    }

    assert set(DEMO_TABLE_REGISTRY) == tenant_tables
    assert all(policy.purge for policy in DEMO_TABLE_REGISTRY.values())


def test_clone_is_a_strict_subset_of_the_purge_registry():
    clone_tables = {
        policy.table for policy in DEMO_TABLE_REGISTRY.values() if policy.clone
    }
    purge_tables = {
        policy.table for policy in DEMO_TABLE_REGISTRY.values() if policy.purge
    }

    assert clone_tables < purge_tables
    assert "documents" in clone_tables
    assert "chunks" in clone_tables
    assert "usage_records" not in clone_tables
    assert "demo_sessions" not in clone_tables


def test_sensitive_integration_and_credential_tables_never_clone():
    assert SENSITIVE_NEVER_CLONE <= set(DEMO_TABLE_REGISTRY)
    assert all(not DEMO_TABLE_REGISTRY[table].clone for table in SENSITIVE_NEVER_CLONE)


def test_assistant_runtime_state_is_purged_but_never_cloned():
    runtime_tables = {
        "background_ai_usage_reservations",
        "engagement_packets",
        "prospect_contact_events",
        "prospect_follow_through",
        "prospect_follow_through_events",
    }

    assert runtime_tables <= set(DEMO_TABLE_REGISTRY)
    assert all(DEMO_TABLE_REGISTRY[table].purge for table in runtime_tables)
    assert all(not DEMO_TABLE_REGISTRY[table].clone for table in runtime_tables)


def test_configurable_workflow_data_is_purge_only_legal_work_product():
    workflow_tables = {
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
    }
    assert workflow_tables <= set(DEMO_TABLE_REGISTRY)
    assert all(DEMO_TABLE_REGISTRY[table].purge for table in workflow_tables)
    assert all(not DEMO_TABLE_REGISTRY[table].clone for table in workflow_tables)


def test_mediation_release_grants_clone_with_portal_business_content():
    release_grant_tables = {
        "mediation_document_recipients",
        "mediation_proposal_recipients",
    }

    assert release_grant_tables <= set(DEMO_TABLE_REGISTRY)
    assert all(DEMO_TABLE_REGISTRY[table].clone for table in release_grant_tables)
    assert all(DEMO_TABLE_REGISTRY[table].purge for table in release_grant_tables)
