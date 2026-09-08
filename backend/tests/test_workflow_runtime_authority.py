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
