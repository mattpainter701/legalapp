from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest

from app.services.automation_capabilities import CapabilityError
from app.services.automation_service_contract import (
    ServiceIdentityInput,
    ServiceRuleInput,
    ServiceSchedule,
)
from app.services.automation_services import (
    approve_rule,
    create_identity,
    create_rule,
    set_rule_status,
)
from app.routers import automation_services as router_module
from app.services.workflow_run_contract import RunStepInput, WorkflowRunInput


class Database:
    def __init__(self, rows=()):
        self.scalar = AsyncMock(side_effect=list(rows))
        self.flush = AsyncMock()
        self.add = Mock()
        self.add_all = Mock()


@pytest.mark.asyncio
async def test_create_identity_creates_noninteractive_principal():
    db = Database()
    actor = SimpleNamespace(id=uuid4())
    tenant_id = uuid4()

    identity = await create_identity(
        db,
        tenant_id=tenant_id,
        actor=actor,
        body=ServiceIdentityInput(name="Night prep", capabilities=["propose_task"]),
    )

    user, created = db.add_all.call_args.args[0]
    assert created is identity
    assert user.tenant_id == tenant_id
    assert user.principal_type == "automation_service"
    assert not user.is_active and not user.license_active
    assert not user.workspace_mcp_enabled
    assert identity.created_by_user_id == actor.id
    assert identity.capabilities == ["propose_task"]
    db.flush.assert_awaited_once()


def rule(*, status="draft", version=1):
    return SimpleNamespace(
        id=uuid4(),
        status=status,
        version=version,
        approved_by_user_id=None,
        approved_at=None,
        updated_at=None,
    )


@pytest.mark.asyncio
async def test_approve_rule_requires_live_legal_authority_and_advances_version():
    actor = SimpleNamespace(id=uuid4(), is_active=True, license_active=True)
    row = rule()
    db = Database([row])
    with patch(
        "app.services.automation_services.get_user_capabilities",
        AsyncMock(return_value={"approve_legal_work", "manage_matters"}),
    ):
        returned = await approve_rule(
            db,
            tenant_id=uuid4(),
            actor=actor,
            rule_id=row.id,
            expected_version=1,
        )

    assert returned is row
    assert row.status == "active"
    assert row.version == 2
    assert row.approved_by_user_id == actor.id
    assert row.approved_at.tzinfo is timezone.utc
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("capabilities", "row", "expected_version", "actor_active", "code"),
    [
        (set(), rule(), 1, True, "approval_permission_denied"),
        ({"approve_legal_work"}, None, 1, True, "service_rule_not_found"),
        (
            {"approve_legal_work"},
            rule(version=2),
            1,
            True,
            "service_rule_version_conflict",
        ),
        (
            {"approve_legal_work"},
            rule(status="active"),
            1,
            True,
            "service_rule_not_draft",
        ),
        ({"approve_legal_work"}, rule(), 1, False, "approval_actor_unavailable"),
    ],
)
async def test_approve_rule_rejects_invalid_state(
    capabilities, row, expected_version, actor_active, code
):
    actor = SimpleNamespace(id=uuid4(), is_active=actor_active, license_active=True)
    db = Database([row])
    with (
        patch(
            "app.services.automation_services.get_user_capabilities",
            AsyncMock(return_value=capabilities),
        ),
        pytest.raises(CapabilityError) as error,
    ):
        await approve_rule(
            db,
            tenant_id=uuid4(),
            actor=actor,
            rule_id=uuid4(),
            expected_version=expected_version,
        )
    assert error.value.code == code


@pytest.mark.asyncio
async def test_set_rule_status_only_changes_approved_current_rule():
    actor = SimpleNamespace(id=uuid4())
    row = rule(status="active", version=3)
    db = Database([row])

    returned = await set_rule_status(
        db,
        tenant_id=uuid4(),
        actor=actor,
        rule_id=row.id,
        expected_version=3,
        status="paused",
    )

    assert returned is row
    assert row.status == "paused"
    assert row.version == 4
    assert row.updated_at.tzinfo is timezone.utc


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "row", "expected_version", "code"),
    [
        ("draft", rule(status="active"), 1, "invalid_service_rule_status"),
        ("paused", None, 1, "service_rule_version_conflict"),
        ("paused", rule(version=2), 1, "service_rule_version_conflict"),
        ("paused", rule(status="draft"), 1, "service_rule_not_approved"),
    ],
)
async def test_set_rule_status_rejects_invalid_transitions(
    status, row, expected_version, code
):
    db = Database([row])
    with pytest.raises(CapabilityError) as error:
        await set_rule_status(
            db,
            tenant_id=uuid4(),
            actor=SimpleNamespace(id=uuid4()),
            rule_id=uuid4(),
            expected_version=expected_version,
            status=status,
        )
    assert error.value.code == code


@pytest.mark.asyncio
async def test_router_returns_reviewable_fields_and_commits_mutations():
    tenant_id = uuid4()
    actor = SimpleNamespace(id=uuid4(), tenant_id=tenant_id)
    identity = SimpleNamespace(
        id=uuid4(),
        name="Night prep",
        capabilities=["propose_task"],
        status="active",
        version=1,
        created_at=datetime.now(timezone.utc),
    )
    row = SimpleNamespace(
        id=uuid4(),
        name="Night rule",
        identity_id=identity.id,
        matter_id=uuid4(),
        source_run_id=uuid4(),
        schedule={"kind": "daily", "local_time": "02:00", "timezone": "UTC"},
        status="draft",
        version=1,
        definition_sha256="a" * 64,
        plan_sha256="b" * 64,
        approved_by_user_id=None,
        approved_at=None,
        created_at=datetime.now(timezone.utc),
    )
    db = SimpleNamespace(commit=AsyncMock())
    with (
        patch.object(router_module, "set_tenant_context", AsyncMock()),
        patch.object(
            router_module, "create_identity", AsyncMock(return_value=identity)
        ),
        patch.object(router_module, "create_rule", AsyncMock(return_value=row)),
        patch.object(router_module, "approve_rule", AsyncMock(return_value=row)),
        patch.object(router_module, "set_rule_status", AsyncMock(return_value=row)),
    ):
        created = await router_module.add_identity(
            ServiceIdentityInput(name="Night prep", capabilities=["propose_task"]),
            db,
            actor,
        )
        assert created["id"] == str(identity.id)
        body = SimpleNamespace(identity_id=identity.id, source_run_id=row.source_run_id)
        with patch.object(router_module, "ServiceRuleInput", return_value=body):
            rule = await router_module.add_rule(body, db, actor)
        approved = await router_module.approve(
            row.id, router_module.VersionRequest(expected_version=1), db, actor
        )
        changed = await router_module.status(
            row.id,
            router_module.StatusRequest(expected_version=1, status="paused"),
            db,
            actor,
        )
    assert rule["definition_sha256"] == "a" * 64
    assert approved["approved_by_user_id"] is None
    assert changed["schedule"]["kind"] == "daily"
    assert db.commit.await_count == 4


def test_router_maps_not_found_and_conflicts():
    missing = router_module._error(CapabilityError("service_rule_not_found", "gone"))
    conflict = router_module._error(CapabilityError("service_scope_denied", "no"))
    assert missing.status_code == 404
    assert conflict.status_code == 409


@pytest.mark.asyncio
async def test_create_rule_rejects_missing_identity_or_run():
    body = ServiceRuleInput(
        name="Rule",
        identity_id=uuid4(),
        source_run_id=uuid4(),
        schedule=ServiceSchedule(kind="daily", local_time="02:00"),
    )
    for rows in ([None, SimpleNamespace()], [SimpleNamespace(status="active"), None]):
        db = Database(rows)
        with pytest.raises(CapabilityError) as error:
            await create_rule(db, tenant_id=uuid4(), actor=SimpleNamespace(id=uuid4()), body=body)
        assert error.value.code == "service_source_unavailable"


@pytest.mark.asyncio
async def test_create_rule_rejects_disabled_identity():
    body = ServiceRuleInput(
        name="Rule",
        identity_id=uuid4(),
        source_run_id=uuid4(),
        schedule=ServiceSchedule(kind="daily", local_time="02:00"),
    )
    identity = SimpleNamespace(id=body.identity_id, status="disabled", version=1)
    run = SimpleNamespace(id=body.source_run_id, matter_id=uuid4(), status="completed")
    db = Database([identity, run])
    with pytest.raises(CapabilityError) as error:
        await create_rule(db, tenant_id=uuid4(), actor=SimpleNamespace(id=uuid4()), body=body)
    assert error.value.code == "service_identity_disabled"


@pytest.mark.asyncio
async def test_create_rule_rejects_unavailable_event_rule():
    from app.models.workflow_automation import MatterWorkflowAutomationRule

    event_rule_id = uuid4()
    body = ServiceRuleInput(
        name="Rule",
        identity_id=uuid4(),
        source_run_id=uuid4(),
        schedule=ServiceSchedule(kind="workflow_event", event_rule_id=event_rule_id),
    )
    identity = SimpleNamespace(
        id=body.identity_id, status="active", version=1, capabilities=["propose_task"]
    )
    run = SimpleNamespace(
        id=body.source_run_id,
        tenant_id=uuid4(),
        matter_id=uuid4(),
        status="completed",
        objective="prepare_document",
        plan_json={"source_bindings": []},
        source_context_ciphertext=None,
    )
    plan = WorkflowRunInput(
        request_id=uuid4(),
        matter_id=run.matter_id,
        objective="prepare_document",
        steps=[
            RunStepInput(
                step_key="task",
                capability="propose_task",
                arguments={"matter_id": str(run.matter_id), "title": "x"},
            )
        ],
    )
    for event_rule in (None, SimpleNamespace(template_id=None, definition_sha256="e" * 64)):
        db = Database([identity, run, event_rule])
        with (
            patch(
                "app.services.automation_services.freeze_service_plan",
                new_callable=AsyncMock,
                return_value=(plan, None),
            ),
            pytest.raises(CapabilityError) as error,
        ):
            await create_rule(db, tenant_id=uuid4(), actor=SimpleNamespace(id=uuid4()), body=body)
        assert error.value.code == "service_event_rule_unavailable"


@pytest.mark.asyncio
async def test_create_rule_freezes_plan_and_hashes_definition():
    from app.services import automation_services as services

    body = ServiceRuleInput(
        name="Rule",
        identity_id=uuid4(),
        source_run_id=uuid4(),
        schedule=ServiceSchedule(kind="daily", local_time="02:00"),
    )
    identity = SimpleNamespace(
        id=body.identity_id,
        status="active",
        version=3,
        capabilities=["propose_task"],
    )
    run = SimpleNamespace(id=body.source_run_id, matter_id=uuid4(), status="completed")
    plan = WorkflowRunInput(
        request_id=uuid4(),
        matter_id=run.matter_id,
        objective="prepare_document",
        steps=[
            RunStepInput(
                step_key="task",
                capability="propose_task",
                arguments={"matter_id": str(run.matter_id), "title": "x"},
            )
        ],
    )
    db = Database([identity, run])
    with (
        patch.object(services, "freeze_service_plan", new_callable=AsyncMock, return_value=(plan, None)),
        patch.object(services, "seal_payload", return_value=("ciphertext", "payloadsha")),
        patch.object(services, "digest_payload", return_value="a" * 64),
    ):
        rule = await services.create_rule(
            db, tenant_id=uuid4(), actor=SimpleNamespace(id=uuid4()), body=body
        )

    assert rule.identity_id == identity.id
    assert rule.status == "draft"
    assert rule.plan_sha256 == "a" * 64
    assert rule.definition_sha256 == "a" * 64
    assert rule.event_rule_id is None
    db.add.assert_called_once_with(rule)
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_rule_with_event_rule_freezes_event_hash():
    from app.services import automation_services as services

    event_rule_id = uuid4()
    body = ServiceRuleInput(
        name="Event rule",
        identity_id=uuid4(),
        source_run_id=uuid4(),
        schedule=ServiceSchedule(kind="workflow_event", event_rule_id=event_rule_id),
    )
    identity = SimpleNamespace(
        id=body.identity_id,
        status="active",
        version=1,
        capabilities=["propose_task"],
    )
    run = SimpleNamespace(id=body.source_run_id, matter_id=uuid4(), status="completed")
    plan = WorkflowRunInput(
        request_id=uuid4(),
        matter_id=run.matter_id,
        objective="prepare_document",
        steps=[
            RunStepInput(
                step_key="task",
                capability="propose_task",
                arguments={"matter_id": str(run.matter_id), "title": "x"},
            )
        ],
    )
    event_rule = SimpleNamespace(template_id=uuid4(), definition_sha256="e" * 64)
    db = Database([identity, run, event_rule])
    with (
        patch.object(services, "freeze_service_plan", new_callable=AsyncMock, return_value=(plan, None)),
        patch.object(services, "seal_payload", return_value=("ciphertext", "payloadsha")),
        patch.object(services, "digest_payload", return_value="a" * 64),
    ):
        rule = await services.create_rule(
            db, tenant_id=uuid4(), actor=SimpleNamespace(id=uuid4()), body=body
        )

    assert rule.event_rule_id == event_rule_id
    assert rule.event_rule_sha256 == event_rule.definition_sha256


@pytest.mark.asyncio
async def test_router_list_services_returns_identities_and_rules():
    tenant_id = uuid4()
    actor = SimpleNamespace(id=uuid4(), tenant_id=tenant_id)
    db = SimpleNamespace(
        scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [])),
        commit=AsyncMock(),
    )
    with patch.object(router_module, "set_tenant_context", new_callable=AsyncMock):
        result = await router_module.list_services(db, actor)
    assert result == {"identities": [], "rules": []}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint,kwargs",
    [
        ("add_identity", {"body": ServiceIdentityInput(name="x", capabilities=["propose_task"])}),
        ("add_rule", {"body": SimpleNamespace(identity_id=uuid4(), source_run_id=uuid4())}),
        ("approve", {"rule_id": uuid4(), "body": SimpleNamespace(expected_version=1)}),
        ("status", {"rule_id": uuid4(), "body": SimpleNamespace(expected_version=1, status="paused")}),
    ],
)
async def test_router_endpoints_map_capability_errors(endpoint, kwargs):
    tenant_id = uuid4()
    actor = SimpleNamespace(id=uuid4(), tenant_id=tenant_id)
    db = SimpleNamespace(commit=AsyncMock())
    with (
        patch.object(router_module, "set_tenant_context", new_callable=AsyncMock),
        (
            patch.object(
                router_module,
                "create_identity" if endpoint == "add_identity" else "create_rule",
                new_callable=AsyncMock,
                side_effect=CapabilityError("service_source_unavailable", "no"),
            )
            if endpoint in {"add_identity", "add_rule"}
            else patch.object(
                router_module,
                "approve_rule" if endpoint == "approve" else "set_rule_status",
                new_callable=AsyncMock,
                side_effect=CapabilityError("service_rule_version_conflict", "no"),
            )
        ),
    ):
        with pytest.raises(router_module.HTTPException) as error:
            coro = getattr(router_module, endpoint)
            if endpoint in {"approve", "status"}:
                await coro(kwargs["rule_id"], kwargs["body"], db, actor)
            else:
                await coro(kwargs["body"], db, actor)
    assert error.value.status_code == 409
