from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from jose import jwt
from starlette.requests import Request

from app.models.workspace_mcp_grant import WorkspaceMCPGrant
from app.schemas.chat_action import (
    FindMatterArgs,
    ProposeClientEmailArgs,
    ProposeMatterDocumentArgs,
    ProposeTaskArgs,
)
from app.services import workspace_mcp_protocol as protocol
from app.services.automation_capabilities import CapabilityError
from app.services.workspace_mcp_grants import (
    WorkspaceMCPGrantError,
    require_active_workspace_grant,
)


def identity(*, scopes: set[str] | None = None, capabilities: set[str] | None = None):
    return protocol.WorkspaceMCPIdentity(
        user_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        client_id="test-client",
        grant_id=str(uuid.uuid4()),
        token_id="test-token",
        scopes=frozenset({"matters:read"} if scopes is None else scopes),
        app_capabilities=frozenset(
            {"manage_matters"} if capabilities is None else capabilities
        ),
    )


class FakeDB:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


class SessionContext:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *_args):
        return False


def test_protocol_helpers_filter_origins_scopes_and_annotations():
    assert protocol._origin_and_host("https://lawhand.example/mcp") == (
        "https://lawhand.example",
        "lawhand.example",
    )
    assert protocol._origin_and_host("not a url") == (None, None)
    assert protocol._claim_scopes("matters:read tasks:read") == frozenset(
        {"matters:read", "tasks:read"}
    )
    with pytest.raises(HTTPException):
        protocol._claim_scopes(["not-a-known-scope"])
    with pytest.raises(HTTPException):
        protocol._claim_scopes(None)

    spec = protocol.resolve_capability_spec("find_matter")
    tool = protocol._as_mcp_tool(spec)
    assert tool.name == "find_matter"
    assert tool.annotations.readOnlyHint is True
    assert protocol._identity_allows(identity(), spec)
    assert not protocol._identity_allows(identity(scopes=set()), spec)


@pytest.mark.asyncio
async def test_list_tools_and_call_tool_cover_success_denial_and_errors(monkeypatch):
    request = Request({"type": "http", "headers": []})
    current = identity(scopes={"matters:read", "tasks:propose"})
    monkeypatch.setattr(protocol, "_request_and_identity", lambda: (request, current))
    tools = await protocol.list_workspace_tools()
    assert {item.name for item in tools} == {"find_matter", "propose_task"}

    async def successful(**kwargs):
        return {
            "status": "review",
            "request_id": kwargs["request"].headers.get("X-Request-ID"),
        }

    monkeypatch.setattr(protocol, "execute_workspace_capability", successful)
    result = await protocol.call_workspace_tool("propose_task", {"title": "x"})
    assert result.isError is False
    assert result.structuredContent["status"] == "review"

    denied = await protocol.call_workspace_tool("propose_matter_document", {})
    assert denied.isError is True
    assert denied.structuredContent["error"]["code"] == "capability_scope_denied"

    async def bad(**_kwargs):
        raise CapabilityError("invalid_tool_arguments", "bad input")

    monkeypatch.setattr(protocol, "execute_workspace_capability", bad)
    failed = await protocol.call_workspace_tool("find_matter", {})
    assert failed.structuredContent["error"]["code"] == "invalid_tool_arguments"

    unknown = await protocol.call_workspace_tool("approve_task", {})
    assert unknown.structuredContent["error"]["code"] == "unsupported_tool"


@pytest.mark.asyncio
async def test_execute_capability_dispatches_and_commits_only_proposals(monkeypatch):
    db = FakeDB()
    user = SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4())
    actor = (user, frozenset({"manage_matters"}))
    monkeypatch.setattr(protocol, "set_tenant_context", lambda *_args: _async_noop())
    monkeypatch.setattr(
        protocol, "_load_workspace_actor", lambda *_args: _async_value(actor)
    )
    monkeypatch.setattr(protocol, "async_session_maker", lambda: SessionContext(db))
    monkeypatch.setattr(
        protocol,
        "append_workspace_mcp_audit",
        lambda *_args, **_kwargs: _async_noop(),
    )

    async def handler(context, parsed):
        assert context.channel == "workspace_mcp"
        assert parsed.title == "Draft task"
        return {"ok": True}

    from app.services.chat_tools import handlers

    monkeypatch.setattr(handlers, "propose_task", handler)
    result = await protocol.execute_workspace_capability(
        name="propose_task",
        arguments={"matter_id": str(uuid.uuid4()), "title": "Draft task"},
        request=Request({"type": "http", "headers": [(b"x-request-id", b"req-1")]}),
        identity=identity(scopes={"matters:read", "tasks:propose"}),
    )
    assert result == {"ok": True}
    assert db.commits == 1
    assert db.rollbacks == 0

    with pytest.raises(CapabilityError, match="invalid"):
        await protocol.execute_workspace_capability(
            name="propose_task",
            arguments={"title": "missing matter"},
            request=Request({"type": "http", "headers": []}),
            identity=identity(scopes={"matters:read", "tasks:propose"}),
        )


async def _async_noop():
    return None


async def _async_value(value):
    return value


def test_chat_action_contracts_reject_invented_recipients_and_bound_documents():
    matter_id = uuid.uuid4()
    assert FindMatterArgs(query="  client  ").query.strip() == "client"
    with pytest.raises(ValueError):
        ProposeTaskArgs(matter_id=matter_id, title="")
    with pytest.raises(ValueError):
        ProposeClientEmailArgs(
            matter_id=matter_id,
            recipient_party_ids=[uuid.uuid4()],
            title="Status",
            subject="Status",
            body="Body",
            to=["attacker@example.com"],
        )
    with pytest.raises(ValueError):
        ProposeMatterDocumentArgs(matter_id=matter_id, title="Draft", body="")


class GrantDB:
    def __init__(self, grant):
        self.grant = grant

    async def scalar(self, _statement):
        return self.grant


def grant(**overrides):
    now = datetime.now(timezone.utc)
    values = {
        "id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "client_id": "codex",
        "scopes": ["matters:read"],
        "status": "active",
        "expires_at": now + timedelta(minutes=5),
        "revoked_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(
        **values,
        is_active=lambda moment: values["status"] == "active"
        and values["revoked_at"] is None
        and values["expires_at"] > moment,
        scope_set=frozenset(values["scopes"]),
        last_used_at=None,
    )


@pytest.mark.asyncio
async def test_grant_validation_rejects_bad_id_and_records_last_use():
    with pytest.raises(WorkspaceMCPGrantError, match="invalid"):
        await require_active_workspace_grant(
            GrantDB(None),
            grant_id="bad",
            tenant_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            client_id="codex",
            token_scopes=frozenset({"matters:read"}),
        )
    item = grant()
    now = datetime.now(timezone.utc)
    result = await require_active_workspace_grant(
        GrantDB(item),
        grant_id=str(item.id),
        tenant_id=item.tenant_id,
        user_id=item.user_id,
        client_id=item.client_id,
        token_scopes=frozenset({"matters:read"}),
        now=now,
    )
    assert result is item
    assert item.last_used_at == now


def test_grant_model_is_fail_closed_for_bad_scopes_and_revocation():
    model = WorkspaceMCPGrant(
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        client_id="codex",
        client_name="Codex",
        scopes="not-a-list",
        consent_version="v1",
        consent_sha256="a" * 64,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=2),
        status="active",
    )
    assert model.scope_set == frozenset()
    assert model.is_active()
    model.revoked_at = datetime.now(timezone.utc)
    assert not model.is_active()


def test_decode_workspace_token_rejects_wrong_type_and_unknown_scope(monkeypatch):
    issuer = "issuer"
    audience = "audience"
    key = "k" * 48
    monkeypatch.setattr(protocol.settings, "WORKSPACE_MCP_ISSUER", issuer)
    monkeypatch.setattr(protocol.settings, "WORKSPACE_MCP_AUDIENCE", audience)
    monkeypatch.setattr(protocol.settings, "WORKSPACE_MCP_TOKEN_SIGNING_KEY", key)
    base = {
        "iss": issuer,
        "aud": audience,
        "sub": str(uuid.uuid4()),
        "tenant_id": str(uuid.uuid4()),
        "client_id": "client",
        "grant_id": str(uuid.uuid4()),
        "jti": "jti",
        "scope": "unknown:scope",
        "iat": int(time.time()),
        "exp": int(time.time()) + 120,
        "type": "wrong",
        "token_use": "access",
    }
    token = jwt.encode(base, key, algorithm=protocol.settings.ALGORITHM)
    with pytest.raises(HTTPException, match="Invalid workspace access token"):
        protocol.decode_workspace_access_token(token)
