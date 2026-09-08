from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.routers import artifact_reviews as routes
from app.services import task_workflow


@pytest.mark.asyncio
async def test_policy_is_readable_but_editing_requires_both_capabilities(monkeypatch):
    monkeypatch.setattr(routes, "set_tenant_context", AsyncMock())
    monkeypatch.setattr(
        routes, "artifact_review_policy", AsyncMock(return_value="attorney_only")
    )
    caps = AsyncMock(return_value={"approve_legal_work"})
    monkeypatch.setattr(routes, "get_user_capabilities", caps)
    user = NS(id=uuid4(), tenant_id=uuid4())
    assert await routes.get_policy(user, AsyncMock()) == {
        "policy": "attorney_only",
        "can_edit": False,
    }
    caps.return_value = {"manage_users", "approve_legal_work"}
    assert (await routes.get_policy(user, AsyncMock()))["can_edit"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "existing", [None, NS(artifact_review_policy="staff_then_attorney")]
)
async def test_policy_update_is_audited_and_does_not_rewrite_reviews(
    monkeypatch, existing
):
    monkeypatch.setattr(routes, "set_tenant_context", AsyncMock())
    monkeypatch.setattr(
        routes, "artifact_review_policy", AsyncMock(return_value="staff_then_attorney")
    )
    user = NS(id=uuid4(), tenant_id=uuid4())
    db = AsyncMock()
    db.scalar.side_effect = [NS(), existing]
    rows = []
    db.add = rows.append
    result = await routes.update_policy(
        routes.PolicyUpdate(
            policy="attorney_only", expected_policy="staff_then_attorney"
        ),
        user,
        db,
    )
    assert result["policy"] == "attorney_only"
    audit = rows[-1]
    assert audit.actor_id == str(user.id)
    assert audit.metadata_json["previous_policy"] == "staff_then_attorney"
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_stale_policy_is_rejected_without_commit(monkeypatch):
    monkeypatch.setattr(routes, "set_tenant_context", AsyncMock())
    monkeypatch.setattr(
        routes, "artifact_review_policy", AsyncMock(return_value="attorney_only")
    )
    db = AsyncMock()
    with pytest.raises(HTTPException) as error:
        await routes.update_policy(
            routes.PolicyUpdate(
                policy="attorney_only", expected_policy="staff_then_attorney"
            ),
            NS(id=uuid4(), tenant_id=uuid4()),
            db,
        )
    assert error.value.status_code == 409
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_history_returns_only_live_actor_actions(monkeypatch):
    monkeypatch.setattr(routes, "set_tenant_context", AsyncMock())
    require = AsyncMock(
        side_effect=[
            task_workflow.TaskWorkflowError("no", 403),
            None,
            task_workflow.TaskWorkflowError("no", 409),
        ]
    )
    monkeypatch.setattr(routes, "require_review_actor", require)
    db = AsyncMock()
    artifact = NS(id=uuid4(), task_id=uuid4(), current_revision_no=2)
    task = NS(version=8, review_policy="attorney_only")
    db.scalar.side_effect = [artifact, task]
    db.scalars.return_value = NS(all=lambda: [])
    result = await routes.review_history(
        artifact.id, NS(id=uuid4(), tenant_id=uuid4()), db
    )
    assert result["available_actions"] == ["attorney"]
    assert result["task_version"] == 8
    assert result["requirements"] == []
    db.scalar.side_effect = [None]
    with pytest.raises(HTTPException) as error:
        await routes.review_history(uuid4(), NS(id=uuid4(), tenant_id=uuid4()), db)
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_attorney_only_stage_approves_and_resets_without_staff(monkeypatch):
    staff, attorney = uuid4(), uuid4()
    task = NS(
        id=uuid4(),
        tenant_id=uuid4(),
        status="review",
        version=1,
        pending_action=None,
        staff_reviewed_at=None,
        staff_reviewed_by_user_id=None,
        attorney_approved_at=None,
        attorney_approved_by_user_id=None,
        attorney_override=False,
        attorney_reviewer_user_id=attorney,
    )
    db = NS(add=lambda value: None)
    task.review_policy = "attorney_only"
    task.review_stage = "attorney_pending"
    task.reviewer_user_id = attorney
    task.staff_reviewer_user_id = None
    monkeypatch.setattr(
        task_workflow, "_is_attorney_capable", AsyncMock(return_value=True)
    )
    with pytest.raises(task_workflow.TaskWorkflowError):
        await task_workflow.require_review_actor(db, task, NS(id=staff), stage="staff")
    with pytest.raises(task_workflow.TaskWorkflowError):
        await task_workflow.require_review_actor(
            db, task, NS(id=attorney), stage="attorney", override=True
        )
    await task_workflow.record_review_decision(
        db, task, actor=NS(id=attorney), stage="attorney", decision="approve"
    )
    assert task_workflow.staged_review_is_approved(task)
    assert task_workflow.reset_staged_review_after_edit(task)
    assert not task_workflow.staged_review_is_approved(task)
    assert task.review_stage == "attorney_pending" and task.reviewer_user_id == attorney
    await task_workflow.record_review_decision(
        db,
        task,
        actor=NS(id=attorney),
        stage="attorney",
        decision="request_changes",
        reason="Correct dates",
    )
    assert task.review_stage == "attorney_pending"
