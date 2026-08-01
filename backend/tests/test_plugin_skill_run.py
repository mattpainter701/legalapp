from app.models.plugin_skill_run import PluginSkillRun
from app.schemas.plugin import SkillResponse


def test_plugin_skill_run_keeps_digest_not_raw_input():
    columns = set(PluginSkillRun.__table__.columns.keys())

    assert "input_digest" in columns
    assert "input_text" not in columns
    assert {"memo", "matter_id", "review_status", "reviewed_at"}.issubset(columns)


def test_skill_response_identifies_saved_draft():
    response = SkillResponse(
        skill="saas-review",
        plugin="commercial-legal",
        memo="Review memo",
        model_used="demo-model",
        run_id="00000000-0000-0000-0000-000000000001",
    )

    assert response.review_status == "draft"
    assert response.requires_attorney_review is True
