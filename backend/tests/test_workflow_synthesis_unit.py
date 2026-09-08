"""History collection and live authorization boundaries before draft generation."""

from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException, UploadFile
import io

from app.services import workflow_synthesis as synthesis
from app.routers import workflow_synthesis as api


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state,code",
    [
        ("missing", "actor_unavailable"),
        ("inactive", "actor_unavailable"),
        ("unlicensed", "actor_unavailable"),
        ("permission", "actor_permission_changed"),
        ("import_permission", "import_permission_required"),
    ],
)
async def test_rechecks_actor_before_reading_any_history(monkeypatch, state, code):
    actor = SimpleNamespace(
        is_active=state != "inactive", license_active=state != "unlicensed"
    )
    db = AsyncMock()
    db.scalar.return_value = None if state == "missing" else actor
    monkeypatch.setattr(synthesis, "acquire_workflow_config_lock", AsyncMock())
    monkeypatch.setattr(
        synthesis,
        "get_user_capabilities",
        AsyncMock(
            return_value=set()
            if state == "permission"
            else {"manage_matters", "manage_workflows"}
        ),
    )
    observed = AsyncMock()
    monkeypatch.setattr(synthesis, "live_observations", observed)
    job = SimpleNamespace(
        tenant_id=uuid4(),
        payload={
            "actor_user_id": str(uuid4()),
            "import_run_id": str(uuid4()) if state == "import_permission" else None,
        },
    )
    result = await synthesis.run_synthesis_job(db, job)
    assert result == {"outcome": "blocked", "failure_code": code}
    observed.assert_not_called()


@pytest.mark.asyncio
async def test_document_usage_review_roles_and_correspondence_use_metadata_only():
    tenant, actor, matter = uuid4(), uuid4(), uuid4()
    now = datetime(2026, 8, 1, tzinfo=timezone.utc)
    task = dict(
        id=uuid4(),
        title="Call Private Matter",
        task_type="legacy",
        due_date=None,
        created_at=now,
        assigned_to_user_id=actor,
        review_policy="staff_then_attorney",
        matter_id=matter,
        matter_name="Private Matter",
        matter_type="probate",
        practice_area=None,
        stage="Open",
        opened_at=now,
        user_id=uuid4(),
        attorney_of_record_id=actor,
    )
    document = dict(
        id=uuid4(),
        kind="draft",
        created_at=now,
        matter_id=matter,
        matter_type="probate",
        practice_area=None,
        stage="Open",
        opened_at=now,
        template_id=uuid4(),
        template_sha256="a" * 64,
        template_name="Inventory",
        review_policy="attorney_only",
    )
    mail = dict(
        id=uuid4(),
        channel="email",
        occurred_at=now,
        matter_id=matter,
        matter_type="probate",
        practice_area=None,
        stage="Open",
        opened_at=now,
    )
    results = []
    for rows in ([task], [document], [mail, {**mail, "id": uuid4()}]):
        result = Mock()
        result.mappings.return_value.all.return_value = rows
        results.append(result)
    db = AsyncMock()
    db.execute.side_effect = results
    observations, summary = await synthesis.live_observations(
        db, tenant, date(2026, 9, 8)
    )
    assert len(observations) == 4 and summary["document_records"] == 1
    assert (
        observations[0].assignee_role == "attorney_of_record"
        and observations[0].task_type == "general"
    )
    assert (
        observations[1].title == "Prepare Inventory"
        and observations[1].review_policy == "attorney_only"
    )
    assert (
        observations[2].title == "Prepare client email"
        and observations[3].title == "Follow up with client by email"
    )
    for call in db.execute.call_args_list:
        sql = str(call.args[0]).lower()
        assert "body" not in sql and "content_text" not in sql and "summary" not in sql
        assert call.args[1]["tenant"] == tenant


@pytest.mark.asyncio
async def test_invalid_csv_and_mapping_never_touch_storage():
    with pytest.raises(HTTPException) as error:
        await api.preview_history(UploadFile(io.BytesIO(b"")), None, object())
    assert error.value.status_code == 422
    db = AsyncMock()
    for mapping in ("[]", '{"tasks":[]}', "not-json"):
        with pytest.raises(HTTPException) as error:
            content = b"a\nx\n"
            await api.upload_history(
                UploadFile(io.BytesIO(content)),
                None,
                "clio",
                mapping,
                api.file_fingerprint(content, None),
                db,
                object(),
            )
        assert error.value.status_code == 422
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_decline_rejects_blank_reason_and_cross_tenant_missing_record(
    monkeypatch,
):
    db = AsyncMock()
    db.scalar.return_value = None
    actor = SimpleNamespace(id=uuid4(), tenant_id=uuid4())
    monkeypatch.setattr(api, "set_tenant_context", AsyncMock())
    monkeypatch.setattr(api, "acquire_workflow_config_lock", AsyncMock())
    for reason, code in ((" ", 422), ("Keep current process", 404)):
        with pytest.raises(HTTPException) as error:
            await api.decline(
                uuid4(),
                api.DeclineRequest(expected_proposal_sha256="a" * 64, reason=reason),
                db,
                actor,
            )
        assert error.value.status_code == code
    db.commit.assert_not_called()
