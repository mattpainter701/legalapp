import uuid

import pytest

from app.services.workspace_mcp_access import tenant_workspace_mcp_default


class _DB:
    def __init__(self, value):
        self.value = value
        self.statements = []

    async def scalar(self, statement):
        self.statements.append(statement)
        return self.value


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
