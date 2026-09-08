from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.routers import workspace_mcp_activity as api
from app.models.workspace_mcp_grant import WorkspaceMCPGrant
from app.models.workspace_mcp_audit import WorkspaceMCPAuditEvent
from app.models.tenant import Tenant


def result(rows):
    return NS(all=lambda: rows)


def test_metadata_drops_work_product_and_invalid_identifiers():
    task = str(uuid4())
    assert api.evidence_metadata(
        {
            "task_id": task,
            "artifact_id": "bad",
            "body": "private",
            "query": "secret",
            "result_bytes": 32,
            "duration_ms": -1,
            "result_count": True,
            "effect": "read",
            "failure_reason": "rate_limited",
        }
    ) == {
        "task_id": task,
        "result_bytes": 32,
        "effect": "read",
        "failure_reason": "rate_limited",
    }
    assert api.evidence_metadata(None) == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "capabilities,status",
    [
        (set(), 403),
        ({"admin_settings"}, 403),
        ({"manage_matters", "manage_documents"}, 403),
        ({"admin_settings", "manage_matters", "manage_documents"}, 200),
    ],
)
async def test_firm_activity_requires_all_permissions(
    monkeypatch, capabilities, status
):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from app.services import access_control, rbac_service
    from app.database import get_db

    app = FastAPI()
    app.include_router(api.router)
    db = NS(execute=AsyncMock(return_value=result([])))

    async def database():
        yield db

    app.dependency_overrides[get_db] = database
    monkeypatch.setattr(
        access_control,
        "get_current_user",
        AsyncMock(return_value=NS(id=uuid4(), tenant_id=uuid4(), role="admin")),
    )
    monkeypatch.setattr(
        rbac_service, "get_user_capabilities", AsyncMock(return_value=capabilities)
    )
    monkeypatch.setattr(api, "set_tenant_context", AsyncMock())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        for path in (
            "/api/workspace-mcp/activity",
            "/api/workspace-mcp/activity/grants",
        ):
            assert (await client.get(path)).status_code == status


@pytest.mark.asyncio
async def test_empty_page(monkeypatch):
    monkeypatch.setattr(api, "set_tenant_context", AsyncMock())
    db = NS(execute=AsyncMock(return_value=result([])), scalars=AsyncMock())
    user = NS(tenant_id=uuid4())
    assert await api.activity(
        before=None, grant_id=None, limit=25, db=db, user=user
    ) == {"items": [], "reviews_truncated": False, "next_before": None}
    db.scalars.assert_not_awaited()
    api.set_tenant_context.assert_awaited_once_with(db, str(user.tenant_id))


@pytest.mark.asyncio
async def test_activity_joins_exact_evidence_and_paginates(monkeypatch):
    monkeypatch.setattr(api, "set_tenant_context", AsyncMock())
    now = datetime.now(timezone.utc)
    run_id, artifact_id, task_id, tenant_id = uuid4(), uuid4(), uuid4(), uuid4()
    event = NS(
        id=uuid4(),
        user_id=uuid4(),
        grant_id=uuid4(),
        client_id="assistant",
        event_type="tool_called",
        tool_name="propose_workflow_run",
        outcome="success",
        created_at=now,
        chain_position=3,
        event_hash="a" * 64,
        prev_event_hash="b" * 64,
        metadata_json={"run_id": str(run_id), "body": "private"},
    )
    run = NS(id=run_id, status="awaiting_review", matter_id=uuid4())
    step = NS(run_id=run_id, artifact_id=artifact_id, task_id=task_id)
    artifact = NS(
        id=artifact_id, task_id=task_id, status="review", current_revision_no=2
    )
    review = NS(
        id=uuid4(),
        artifact_id=artifact_id,
        revision_id=uuid4(),
        reviewer_user_id=uuid4(),
        decision="approved",
        content_sha256="c" * 64,
        document_sha256="d" * 64,
        created_at=now,
    )
    task = NS(id=task_id, status="review", matter_id=run.matter_id)
    db = NS(
        execute=AsyncMock(
            return_value=result([(event, "Attorney"), (event, "Attorney")])
        ),
        scalars=AsyncMock(
            side_effect=[
                result([run]),
                result([step]),
                result([artifact]),
                result([review]),
                result([task]),
            ]
        ),
    )
    page = await api.activity(
        before=4, grant_id=event.grant_id, limit=1, db=db, user=NS(tenant_id=tenant_id)
    )
    assert page["next_before"] == 3
    item = page["items"][0]
    assert item["run"]["id"] == str(run_id)
    assert item["tasks"][0]["id"] == str(task_id)
    assert item["artifacts"][0]["revision_no"] == 2
    assert item["reviews"][0]["revision_id"] == str(review.revision_id)
    assert "private" not in str(page)
    for call in db.scalars.call_args_list:
        assert tenant_id in call.args[0].compile().params.values()


@pytest.mark.asyncio
async def test_grant_page_preserves_named_actor_without_secrets(monkeypatch):
    monkeypatch.setattr(api, "set_tenant_context", AsyncMock())
    grant = NS(
        id=uuid4(),
        user_id=uuid4(),
        client_name="Claude",
        client_id="claude",
        scope_set=frozenset({"matters:read"}),
        expires_at=None,
        last_used_at=None,
    )
    db = NS(execute=AsyncMock(return_value=result([(grant, "Attorney")] * 51)))
    page = await api.active_grants(offset=0, db=db, user=NS(tenant_id=uuid4()))
    assert len(page["items"]) == 50 and page["next_offset"] == 50
    assert page["items"][0]["user_name"] == "Attorney"


@pytest.mark.asyncio
async def test_real_queries_isolate_tenant_research_expiry_and_revocation(
    db_session, test_user
):
    now = datetime.now(timezone.utc)
    other = Tenant(
        id=uuid4(), name="Other", domain="other.example.test", billing_tier="payg"
    )
    db_session.add(other)
    await db_session.flush()
    own = test_user.tenant_id
    for index, (tenant, client, status, expiry) in enumerate(
        [
            (own, "active", "active", now + timedelta(days=1)),
            (own, "expired", "active", now - timedelta(days=1)),
            (own, "revoked", "revoked", now + timedelta(days=1)),
            (own, "research.client", "active", now + timedelta(days=1)),
            (other.id, "other", "active", now + timedelta(days=1)),
        ]
    ):
        grant = WorkspaceMCPGrant(
            id=uuid4(),
            tenant_id=tenant,
            user_id=test_user.id,
            client_id=client,
            client_name=client,
            scopes=["matters:read"],
            status=status,
            consent_version="test",
            consent_sha256="a" * 64,
            expires_at=expiry,
        )
        db_session.add(grant)
        await db_session.flush()
        db_session.add(
            WorkspaceMCPAuditEvent(
                tenant_id=tenant,
                user_id=test_user.id,
                grant_id=grant.id,
                client_id=client,
                event_type="tool_called",
                tool_name="find_matter",
                outcome="success",
                metadata_json={"effect": "read"},
                chain_position=index + 1,
                event_hash=f"{index+1:064x}",
            )
        )
    await db_session.flush()
    grants = await api.active_grants(offset=0, db=db_session, user=test_user)
    assert [g["client_id"] for g in grants["items"]] == ["active"]
    page = await api.activity(
        before=None, grant_id=None, limit=2, db=db_session, user=test_user
    )
    assert [e["client_id"] for e in page["items"]] == ["revoked", "expired"]
    second = await api.activity(
        before=page["next_before"],
        grant_id=None,
        limit=2,
        db=db_session,
        user=test_user,
    )
    assert [e["client_id"] for e in second["items"]] == ["active"]
    grant = await db_session.scalar(
        select(WorkspaceMCPGrant).where(WorkspaceMCPGrant.client_id == "expired")
    )
    filtered = await api.activity(
        before=None, grant_id=grant.id, limit=25, db=db_session, user=test_user
    )
    assert len(filtered["items"]) == 1
    assert filtered["items"][0]["client_id"] == "expired"
