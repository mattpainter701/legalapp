import uuid

import pytest

from app.services.workspace_mcp_access import (
    lock_tenant_workspace_mcp_policy,
    tenant_workspace_mcp_default,
    tenant_workspace_mcp_enabled,
)


class _DB:
    def __init__(self, value):
        self.value = value
        self.statements = []

    async def scalar(self, statement):
        self.statements.append(statement)
        return self.value

    async def execute(self, statement, parameters=None):
        self.statements.append((statement, parameters))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stored_value", "expected"),
    [(False, False), (True, True), (None, True)],
)
async def test_tenant_workspace_mcp_default(stored_value, expected):
    db = _DB(stored_value)

    result = await tenant_workspace_mcp_default(db, str(uuid.uuid4()))

    assert result is expected
    assert len(db.statements) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stored_value", "expected"),
    [(False, False), (True, True), (None, True)],
)
async def test_tenant_workspace_mcp_enabled(stored_value, expected):
    db = _DB(stored_value)

    result = await tenant_workspace_mcp_enabled(db, str(uuid.uuid4()))

    assert result is expected
    assert len(db.statements) == 1


@pytest.mark.asyncio
async def test_workspace_policy_lock_is_tenant_scoped():
    tenant_id = uuid.uuid4()
    db = _DB(None)

    await lock_tenant_workspace_mcp_policy(db, tenant_id)

    assert len(db.statements) == 1
    _statement, parameters = db.statements[0]
    assert parameters == {"scope": f"workspace-mcp-policy:{tenant_id}"}
