"""Database-free boundary tests complement the Postgres recovery tests."""

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import durable_workflow_automations as outbox


@pytest.fixture
def context(monkeypatch):
    tenant_id, actor_id, matter_id, rule_id, version_id = [
        uuid.uuid4() for _ in range(5)
    ]
    matter = SimpleNamespace(id=matter_id, tenant_id=tenant_id, archived_at=None)
    rule = SimpleNamespace(
        id=rule_id,
        tenant_id=tenant_id,
        status="active",
        definition_sha256="rule",
        template_id=uuid.uuid4(),
    )
    actor = SimpleNamespace(id=actor_id, is_active=True, license_active=True)
    payload = {
        "rule_id": str(rule_id),
        "matter_id": str(matter_id),
        "actor_user_id": str(actor_id),
        "rule_sha256": "rule",
        "matter_sha256": outbox.digest_payload({}),
        "template_version_id": str(version_id),
        "as_of": "2026-09-06",
        "trigger_event": "matter_created",
        "dedupe_key": "key",
    }
    job = SimpleNamespace(tenant_id=tenant_id, payload=payload)
    db = SimpleNamespace(
        scalar=AsyncMock(side_effect=[rule, matter, actor]), flush=AsyncMock()
    )
    monkeypatch.setattr(outbox.planning, "acquire_workflow_config_lock", AsyncMock())
    monkeypatch.setattr(
        outbox.planning, "_existing_dispatch", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        outbox.planning,
        "latest_approved_version_id",
        AsyncMock(return_value=version_id),
    )
    monkeypatch.setattr(
        outbox, "get_user_capabilities", AsyncMock(return_value={"manage_matters"})
    )
    monkeypatch.setattr(outbox, "matter_snapshot", AsyncMock(return_value=({}, {})))
    monkeypatch.setattr(
        outbox.planning,
        "_record_dispatch",
        MagicMock(return_value=SimpleNamespace(id=uuid.uuid4(), outcome="blocked")),
    )
    return db, job, rule, matter, actor


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["rule", "matter", "actor"])
async def test_missing_tenant_scoped_source_blocks_without_fk_error(context, missing):
    db, job, rule, matter, actor = context
    db.scalar.side_effect = [
        None if missing == "rule" else rule,
        None if missing == "matter" else matter,
        None if missing == "actor" else actor,
    ]
    result = await outbox.run_planning_job(db, job)
    assert result["outcome"] == "blocked"
    outbox.planning._record_dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_template_changed_and_license_revoked_block(context):
    db, job, rule, matter, actor = context
    job.payload["template_version_id"] = str(uuid.uuid4())
    await outbox.run_planning_job(db, job)
    assert (
        outbox.planning._record_dispatch.call_args.kwargs["detail"]["failure_code"]
        == "template_changed"
    )
    db.scalar.side_effect = [rule, matter, actor]
    actor.license_active = False
    await outbox.run_planning_job(db, job)
    assert (
        outbox.planning._record_dispatch.call_args.kwargs["detail"]["failure_code"]
        == "actor_unavailable"
    )


@pytest.mark.asyncio
async def test_planning_uses_original_date_and_infrastructure_rejections_retry(
    context, monkeypatch
):
    from datetime import date
    from fastapi import HTTPException

    db, job, rule, matter, actor = context
    plan = AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4(), outcome="planned"))
    original = outbox.planning._plan_for_rule
    monkeypatch.setattr(outbox.planning, "_plan_for_rule", plan)
    assert (await outbox.run_planning_job(db, job))["outcome"] == "planned"
    # Matter has an eager nullable partner-attorney join. The emitted Postgres
    # lock must target matters, never the nullable joined user relation.
    from sqlalchemy.dialects import postgresql

    matter_sql = str(
        db.scalar.call_args_list[1].args[0].compile(dialect=postgresql.dialect())
    )
    assert "LEFT OUTER JOIN users" in matter_sql
    assert matter_sql.endswith("FOR UPDATE OF matters")
    assert plan.call_args.kwargs["as_of"] == date(2026, 9, 6)
    assert plan.call_args.kwargs["actor_user_id"] == actor.id
    monkeypatch.setattr(outbox.planning, "dedupe_key", lambda *a, **k: "key")
    monkeypatch.setattr(
        outbox.planning,
        "build_preview",
        AsyncMock(side_effect=HTTPException(503, "private detail")),
    )
    with pytest.raises(HTTPException):
        await original(
            db,
            rule,
            matter=matter,
            trigger_event="matter_created",
            actor_user_id=actor.id,
            as_of=date(2026, 9, 6),
        )
    outbox.planning._record_dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_enqueue_preserves_original_date_and_no_content(context, monkeypatch):
    db, job, rule, matter, actor = context
    monkeypatch.setattr(
        outbox.planning, "active_rules_for", AsyncMock(return_value=[rule])
    )
    monkeypatch.setattr(outbox.planning, "rule_matches", lambda *a, **k: True)
    monkeypatch.setattr(outbox.planning, "dedupe_key", lambda *a, **k: "condition")
    enqueue = AsyncMock(return_value=job)
    monkeypatch.setattr(outbox, "enqueue_job", enqueue)
    await outbox.enqueue_matter_event(
        db, matter=matter, trigger_event="matter_created", actor_user_id=actor.id
    )
    payload = enqueue.call_args.kwargs["payload"]
    assert set(payload) == {
        "matter_id",
        "rule_id",
        "actor_user_id",
        "trigger_event",
        "rule_sha256",
        "matter_sha256",
        "template_version_id",
        "as_of",
        "dedupe_key",
    }
    assert payload["actor_user_id"] == str(actor.id)
    with pytest.raises(ValueError):
        await outbox.enqueue_matter_event(
            db, matter=matter, trigger_event="external_send", actor_user_id=actor.id
        )
    outbox.planning.active_rules_for.return_value = []
    assert (
        await outbox.enqueue_matter_event(
            db, matter=matter, trigger_event="matter_created", actor_user_id=actor.id
        )
        == []
    )


@pytest.mark.asyncio
async def test_activity_statuses_are_safe_and_completed_receipts_not_duplicated():
    rows = []
    for status, attempts, result in [
        ("pending", 0, None),
        ("pending", 2, None),
        ("running", 1, None),
        ("failed", 5, None),
        ("completed", 1, {"outcome": "blocked", "failure_code": "actor_unavailable"}),
        ("completed", 1, {"event_id": "receipt"}),
    ]:
        rows.append(
            SimpleNamespace(
                id=uuid.uuid4(),
                payload={
                    "rule_id": "rule",
                    "matter_id": "matter",
                    "trigger_event": "matter_created",
                },
                status=status,
                attempts=attempts,
                max_attempts=5,
                result=result,
                created_at=datetime.now(timezone.utc),
                last_error="SECRET",
            )
        )
    db = SimpleNamespace(
        scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: rows))
    )
    activity = await outbox.pending_activity(
        db, uuid.uuid4(), rule_id="rule", matter_id="matter"
    )
    assert [item["outcome"] for item in activity] == [
        "pending",
        "retrying",
        "running",
        "failed",
        "blocked",
    ]
    assert "SECRET" not in str(activity)
    assert "manual preview" in activity[-1]["detail"]["message"]
