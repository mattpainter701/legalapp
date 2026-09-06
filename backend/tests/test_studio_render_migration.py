"""Static safety contract for the Phase 3 Studio render migration."""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from app.models.studio_render import (
    StudioPreferredRenderEvidence,
    StudioRenderArtifact,
)


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "backend/migrations/versions/150_studio_render_jobs.py"


def test_studio_render_revision_is_the_single_next_head():
    backend = ROOT / "backend"
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "migrations"))
    script = ScriptDirectory.from_config(config)
    revision = script.get_revision("150_studio_render_jobs")

    assert script.get_heads() == ["158_matter_intakes"]
    assert revision.down_revision == "149_firm_memory_source_auth"
    assert (
        script.get_revision("151_fm_native_authz").down_revision
        == "150_studio_render_jobs"
    )
    assert (
        script.get_revision("152_file_open_intents").down_revision
        == "151_fm_native_authz"
    )
    assert (
        script.get_revision("153_sms_lifecycle").down_revision
        == "152_file_open_intents"
    )
    assert (
        script.get_revision("154_matter_document_folders").down_revision
        == "153_sms_lifecycle"
    )
    assert (
        script.get_revision("155_matter_workflow_automations").down_revision
        == "154_matter_document_folders"
    )
    assert (
        script.get_revision("156_document_template_versions").down_revision
        == "155_matter_workflow_automations"
    )
    assert (
        script.get_revision("157_template_pub_lifecycle").down_revision
        == "156_document_template_versions"
    )


def test_studio_render_migration_enforces_tenant_and_evidence_fences():
    source = MIGRATION.read_text(encoding="utf-8")

    for table in (
        "studio_render_artifacts",
        "studio_preferred_render_evidence",
    ):
        assert f'"{table}"' in source
        assert f'_enable_rls("{table}")' in source
    assert "ENABLE ROW LEVEL SECURITY" in source
    assert "FORCE ROW LEVEL SECURITY" in source
    assert "app.current_tenant_id" in source
    assert "uq_durable_jobs_studio_idempotency" in source
    assert "uq_durable_jobs_tenant_id" in source
    assert "uq_studio_snapshots_tenant_id" in source
    assert "fk_studio_render_artifact_job_tenant" in source
    assert "fk_studio_render_artifact_snapshot_contract" in source
    assert "uq_studio_snapshot_render_contract" in source
    assert "fk_studio_preferred_render_exact_evidence" in source
    assert "guard_studio_render_artifact_evidence" in source
    assert "studio_render_artifact_evidence_immutable" in source
    assert "app.studio_demo_purge_tenant_id" in source
    assert "demo.status = 'purging'" in source


def test_studio_render_model_and_migration_column_parity():
    source = MIGRATION.read_text(encoding="utf-8")
    for model in (StudioRenderArtifact, StudioPreferredRenderEvidence):
        assert f'"{model.__tablename__}"' in source
        for column in model.__table__.columns:
            assert f'"{column.name}"' in source
