from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest

from app.services.automation_capabilities import CapabilityError
from app.services.automation_service_contract import ServiceIdentityInput
from app.services.automation_services import (
    approve_rule,
    create_identity,
    set_rule_status,
)
from app.routers import automation_services as router_module


class Database:
    def __init__(self, rows=()):
        self.scalar = AsyncMock(side_effect=list(rows))
        self.flush = AsyncMock()
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
        ({"approve_legal_work"}, rule(version=2), 1, True, "service_rule_version_conflict"),
        ({"approve_legal_work"}, rule(status="active"), 1, True, "service_rule_not_draft"),
        ({"approve_legal_work"}, rule(), 1, False, "approval_actor_unavailable"),
    ],
)
async def test_approve_rule_rejects_invalid_state(
    capabilities, row, expected_version, actor_active, code
):
    actor = SimpleNamespace(id=uuid4(), is_active=actor_active, license_active=True)
    db = Database([row])
    with patch(
        "app.services.automation_services.get_user_capabilities",
        AsyncMock(return_value=capabilities),
    ), pytest.raises(CapabilityError) as error:
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
    with patch.object(router_module, "set_tenant_context", AsyncMock()), patch.object(
        router_module, "create_identity", AsyncMock(return_value=identity)
    ), patch.object(router_module, "create_rule", AsyncMock(return_value=row)), patch.object(
        router_module, "approve_rule", AsyncMock(return_value=row)
    ), patch.object(router_module, "set_rule_status", AsyncMock(return_value=row)):
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
