from types import SimpleNamespace
from uuid import uuid4

import pytest
from starlette.requests import Request

from app.services import workspace_mcp_protocol as protocol
from app.services.chat_tools import handlers


class _DB:
    def __init__(self):
        self.rollbacks = 0
        self.commits = 0

    async def rollback(self):
        self.rollbacks += 1

    async def commit(self):
        self.commits += 1


class _Session:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *_args):
        return False


@pytest.mark.asyncio
async def test_tool_failure_survives_refusal_audit_failure(monkeypatch, caplog):
    request = Request({"type": "http", "headers": []})
    identity = protocol.WorkspaceMCPIdentity(
        user_id=uuid4(),
        tenant_id=uuid4(),
        client_id="desktop-client",
        grant_id=str(uuid4()),
        token_id="token-id",
        scopes=frozenset({"matters:read"}),
        app_capabilities=frozenset({"manage_matters"}),
    )
    user = SimpleNamespace(id=identity.user_id, tenant_id=identity.tenant_id)
    db = _DB()

    async def actor(*_args):
        return user, frozenset({"manage_matters"})

    async def failed_handler(*_args, **_kwargs):
        raise RuntimeError("handler failed")

    async def failed_audit(*_args, **_kwargs):
        raise RuntimeError("audit store unavailable")

    monkeypatch.setattr(protocol, "async_session_maker", lambda: _Session(db))
    monkeypatch.setattr(protocol, "_load_workspace_actor", actor)
    monkeypatch.setattr(protocol, "set_tenant_context", failed_audit)
    monkeypatch.setattr(protocol, "append_workspace_mcp_audit", failed_audit)
    monkeypatch.setattr(handlers, "find_matter", failed_handler)

    with pytest.raises(RuntimeError, match="handler failed"):
        await protocol.execute_workspace_capability(
            name="find_matter",
            arguments={"query": "Smith"},
            request=request,
            identity=identity,
        )

    assert db.rollbacks == 1
    assert db.commits == 0
    assert "Workspace MCP refusal audit could not be recorded" in caplog.text
