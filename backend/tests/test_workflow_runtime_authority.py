from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.services.automation_capabilities import (
    CapabilityError,
    resolve_capability_spec,
)
from app.services import workflow_run_authority as authority
from app.services import workspace_mcp_protocol as protocol
from app.services.durable_job_handlers import resolve_job_handler, JOB_HANDLERS


def run(channel="matter_chat"):
    return NS(
        tenant_id=uuid4(),
        actor_user_id=uuid4(),
        matter_id=uuid4(),
        origin_channel=channel,
        grant_id=uuid4(),
        client_id="client",
        scope_snapshot=["matters:read", "tasks:read"],
    )


@pytest.mark.asyncio
async def test_registry_dispatches_only_explicit_handlers(monkeypatch):
    from app.services import durable_job_handlers as registry

    function = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(
        registry, "import_module", lambda module: NS(run_planning_job=function)
    )
    handler = resolve_job_handler("matter_workflow_plan")
    assert handler.atomic_completion and handler.redact_failure
    db, row = object(), object()
    assert await handler.execute(db, row) == {"ok": True}
    function.assert_awaited_once_with(db, row)
    for value in (None, [], "os.system", "execute"):
        with pytest.raises(ValueError, match="Unsupported durable"):
            resolve_job_handler(value)
    assert all(
        handler.module.startswith("app.services.") for handler in JOB_HANDLERS.values()
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "active,actor_active,licensed,access,caps,code",
    [
        (False, True, True, True, {"manage_matters"}, "inactive_tenant"),
        (True, False, True, True, {"manage_matters"}, "actor_unavailable"),
        (True, True, False, True, {"manage_matters"}, "actor_unavailable"),
        (True, True, True, False, {"manage_matters"}, "matter_access_changed"),
        (True, True, True, True, set(), "actor_permission_changed"),
        (True, True, True, True, {"manage_matters"}, None),
    ],
)
async def test_live_actor_and_matter_authority(
    monkeypatch, active, actor_active, licensed, access, caps, code
):
    row = run()
    actor = NS(
        id=row.actor_user_id,
        tenant_id=row.tenant_id,
        role="user",
        is_active=actor_active,
        license_active=licensed,
    )
    db = NS(scalar=AsyncMock(side_effect=[active, actor]))
    monkeypatch.setattr(
        authority, "get_user_capabilities", AsyncMock(return_value=caps)
    )
    monkeypatch.setattr(authority, "can_access_matter", AsyncMock(return_value=access))
    if code:
        with pytest.raises(CapabilityError) as error:
            await authority.current_context(db, row)
        assert error.value.code == code
    else:
        context = await authority.current_context(
            db, row, resolve_capability_spec("list_matter_tasks")
        )
        assert context.user is actor


@pytest.mark.asyncio
async def test_mcp_origin_revalidates_grant_and_current_permissions(monkeypatch):
    row = run("workspace_mcp")
    actor = NS(id=row.actor_user_id, tenant_id=row.tenant_id, role="user")
    db = NS(scalar=AsyncMock(return_value=True))
    loader = AsyncMock(return_value=(actor, frozenset({"manage_matters"})))
    monkeypatch.setattr(protocol, "_load_workspace_actor", loader)
    monkeypatch.setattr(authority, "can_access_matter", AsyncMock(return_value=True))
    context = await authority.current_context(
        db, row, resolve_capability_spec("list_matter_tasks")
    )
    assert context.grant_id == row.grant_id
    assert loader.call_args.args[1].scopes == frozenset(row.scope_snapshot)
    with pytest.raises(CapabilityError, match="Current permissions"):
        await authority.current_context(
            db, row, resolve_capability_spec("list_matter_documents")
        )
    loader.side_effect = HTTPException(401, "revoked")
    with pytest.raises(CapabilityError) as error:
        await authority.current_context(db, row)
    assert error.value.code == "origin_grant_unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind",
    [
        "matter_document",
        "artifact_revision",
        "document_template",
        "workflow_template_version",
    ],
)
async def test_bound_sources_require_exact_current_digest(kind):
    row = run()
    row.plan_json = {
        "source_bindings": [{"kind": kind, "id": str(uuid4()), "sha256": "a" * 64}]
    }
    value = (
        NS(document_sha256="a" * 64, storage_state="verified")
        if kind == "matter_document"
        else "a" * 64
    )
    db = NS(scalar=AsyncMock(return_value=value))
    await authority.verify_source_bindings(db, row)
    db.scalar.return_value = None
    with pytest.raises(CapabilityError) as error:
        await authority.verify_source_bindings(db, row)
    assert error.value.code == "source_binding_changed"


# ─────────────────────────────────────────────────────────────────────────────
# automation_service origin channel
# ─────────────────────────────────────────────────────────────────────────────


def _automation_run():
    return NS(
        tenant_id=uuid4(),
        actor_user_id=uuid4(),
        matter_id=uuid4(),
        origin_channel="automation_service",
        service_rule_id=uuid4(),
        plan_json={"service_rule_sha256": "a" * 64},
        grant_id=None,
        client_id=None,
        scope_snapshot=[],
    )


def _service_rule(run, **overrides):
    data = dict(
        matter_id=run.matter_id,
        definition_sha256=run.plan_json["service_rule_sha256"],
        identity_id=uuid4(),
        approved_by_user_id=uuid4(),
        status="active",
    )
    data.update(overrides)
    return NS(**data)


@pytest.mark.asyncio
async def test_automation_context_rejects_inactive_tenant(monkeypatch):
    run = _automation_run()
    db = NS(scalar=AsyncMock(return_value=False))
    monkeypatch.setattr(authority, "get_user_capabilities", AsyncMock(return_value=set()))
    monkeypatch.setattr(authority, "can_access_matter", AsyncMock(return_value=True))
    with pytest.raises(CapabilityError) as error:
        await authority.current_context(db, run)
    assert error.value.code == "inactive_tenant"


@pytest.mark.asyncio
async def test_automation_context_rejects_missing_or_mismatched_rule(monkeypatch):
    run = _automation_run()
    for rule in (
        None,
        _service_rule(run, matter_id=uuid4()),
        _service_rule(run, definition_sha256="b" * 64),
    ):
        db = NS(scalar=AsyncMock(side_effect=[True, rule]))
        monkeypatch.setattr(
            authority, "get_user_capabilities", AsyncMock(return_value=set())
        )
        monkeypatch.setattr(authority, "can_access_matter", AsyncMock(return_value=True))
        with pytest.raises(CapabilityError) as error:
            await authority.current_context(db, run)
        assert error.value.code == "service_rule_unavailable"


@pytest.mark.asyncio
async def test_automation_context_rejects_missing_identity(monkeypatch):
    run = _automation_run()
    rule = _service_rule(run)
    db = NS(scalar=AsyncMock(side_effect=[True, rule, None, NS(), NS()]))
    monkeypatch.setattr(
        authority, "get_user_capabilities", AsyncMock(return_value=set())
    )
    monkeypatch.setattr(authority, "can_access_matter", AsyncMock(return_value=True))
    with pytest.raises(CapabilityError) as error:
        await authority.current_context(db, run)
    assert error.value.code == "service_identity_unavailable"


@pytest.mark.asyncio
async def test_automation_context_rejects_missing_service_user(monkeypatch):
    run = _automation_run()
    rule = _service_rule(run)
    identity = NS(
        id=rule.identity_id, user_id=run.actor_user_id, status="active", capabilities=[]
    )
    approver = NS(id=rule.approved_by_user_id, is_active=True, license_active=True)
    db = NS(scalar=AsyncMock(side_effect=[True, rule, identity, None, approver]))
    monkeypatch.setattr(
        authority, "get_user_capabilities", AsyncMock(return_value=set())
    )
    monkeypatch.setattr(authority, "can_access_matter", AsyncMock(return_value=True))
    with pytest.raises(CapabilityError) as error:
        await authority.current_context(db, run)
    assert error.value.code == "service_identity_unavailable"


@pytest.mark.asyncio
async def test_automation_context_rejects_missing_approver(monkeypatch):
    run = _automation_run()
    rule = _service_rule(run)
    identity = NS(
        id=rule.identity_id, user_id=run.actor_user_id, status="active", capabilities=[]
    )
    service_user = NS(
        id=run.actor_user_id,
        tenant_id=run.tenant_id,
        is_active=False,
        license_active=False,
        workspace_mcp_enabled=False,
    )
    db = NS(
        scalar=AsyncMock(side_effect=[True, rule, identity, service_user, None])
    )
    monkeypatch.setattr(
        authority, "get_user_capabilities", AsyncMock(return_value=set())
    )
    monkeypatch.setattr(authority, "can_access_matter", AsyncMock(return_value=True))
    with pytest.raises(CapabilityError) as error:
        await authority.current_context(db, run)
    assert error.value.code == "service_identity_unavailable"


@pytest.mark.asyncio
async def test_automation_context_rejects_weak_or_unauthorized_approver(monkeypatch):
    run = _automation_run()
    rule = _service_rule(run)
    identity = NS(
        id=rule.identity_id,
        user_id=run.actor_user_id,
        status="active",
        capabilities=["propose_task"],
    )
    service_user = NS(
        id=run.actor_user_id,
        tenant_id=run.tenant_id,
        is_active=False,
        license_active=False,
        workspace_mcp_enabled=False,
    )
    approver = NS(
        id=rule.approved_by_user_id,
        tenant_id=run.tenant_id,
        is_active=True,
        license_active=True,
        role="admin",
    )

    for caps, access in [
        ({"approve_legal_work"}, True),
        ({"approve_legal_work", "manage_matters"}, False),
    ]:
        db = NS(
            scalar=AsyncMock(
                side_effect=[True, rule, identity, service_user, approver]
            )
        )
        monkeypatch.setattr(
            authority, "get_user_capabilities", AsyncMock(return_value=caps)
        )
        monkeypatch.setattr(authority, "can_access_matter", AsyncMock(return_value=access))
        with pytest.raises(CapabilityError) as error:
            await authority.current_context(db, run)
        assert error.value.code == "service_approval_unavailable"


@pytest.mark.asyncio
async def test_automation_context_allows_service_run_and_marks_admin_owner(monkeypatch):
    run = _automation_run()
    rule = _service_rule(run)
    identity = NS(
        id=rule.identity_id,
        user_id=run.actor_user_id,
        status="active",
        capabilities=["propose_task"],
    )
    service_user = NS(
        id=run.actor_user_id,
        tenant_id=run.tenant_id,
        is_active=False,
        license_active=False,
        workspace_mcp_enabled=False,
    )
    approver = NS(
        id=rule.approved_by_user_id,
        tenant_id=run.tenant_id,
        is_active=True,
        license_active=True,
        role="admin",
    )
    db = NS(
        scalar=AsyncMock(side_effect=[True, rule, identity, service_user, approver])
    )
    monkeypatch.setattr(
        authority,
        "get_user_capabilities",
        AsyncMock(return_value={"approve_legal_work", "manage_matters"}),
    )
    monkeypatch.setattr(authority, "can_access_matter", AsyncMock(return_value=True))

    context = await authority.current_context(db, run)
    assert context.review_owner_user_id == approver.id
    assert context.review_owner_is_admin is True


@pytest.mark.asyncio
async def test_automation_context_denies_service_capability_outside_grant(monkeypatch):
    from app.services.automation_capabilities import resolve_capability_spec

    run = _automation_run()
    rule = _service_rule(run)
    identity = NS(
        id=rule.identity_id,
        user_id=run.actor_user_id,
        status="active",
        capabilities=["propose_task"],
    )
    service_user = NS(
        id=run.actor_user_id,
        tenant_id=run.tenant_id,
        is_active=False,
        license_active=False,
        workspace_mcp_enabled=False,
    )
    approver = NS(
        id=rule.approved_by_user_id,
        tenant_id=run.tenant_id,
        is_active=True,
        license_active=True,
        role="user",
    )
    db = NS(
        scalar=AsyncMock(side_effect=[True, rule, identity, service_user, approver])
    )
    monkeypatch.setattr(
        authority,
        "get_user_capabilities",
        AsyncMock(return_value={"approve_legal_work", "manage_matters"}),
    )
    monkeypatch.setattr(authority, "can_access_matter", AsyncMock(return_value=True))

    with pytest.raises(CapabilityError) as error:
        await authority.current_context(
            db, run, resolve_capability_spec("propose_document_from_template")
        )
    assert error.value.code == "service_scope_denied"
