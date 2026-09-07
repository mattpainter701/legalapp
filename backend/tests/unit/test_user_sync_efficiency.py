import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.models.user import User
from app.services.user_sync import UserSyncService


class _ScalarRows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class _Result:
    rowcount = 1

    def __init__(self, rows=()):
        self._rows = rows

    def scalars(self):
        return _ScalarRows(self._rows)


class _DB:
    def __init__(self, users):
        self.users = users
        self.added = []
        self.execute_calls = 0
        self.statements = []

    async def execute(self, statement, *args, **kwargs):
        self.execute_calls += 1
        self.statements.append(statement)
        return _Result(self.users) if self.execute_calls == 1 else _Result()

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        return None


class _Response:
    status_code = 200
    text = ""

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class _HTTP:
    def __init__(self, payload):
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, *args, **kwargs):
        return _Response(self.payload)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "payload"),
    [
        (
            "microsoft",
            {"value": [{"id": "ms-1", "mail": "EXISTING@test.com"}]},
        ),
        (
            "google",
            {"users": [{"id": "g-1", "primaryEmail": "EXISTING@test.com"}]},
        ),
    ],
)
async def test_sync_existing_user_does_not_lookup_workspace_default(provider, payload):
    tenant_id = str(uuid.uuid4())
    existing = User(
        id=uuid.uuid4(),
        tenant_id=uuid.UUID(tenant_id),
        email="existing@test.com",
        full_name="Existing",
        is_active=False,
    )
    db = _DB([existing])
    service = UserSyncService()

    with (
        patch("app.services.user_sync.get_fresh_token", new=AsyncMock(return_value="tok")),
        patch(
            "app.services.user_sync.httpx.AsyncClient",
            return_value=_HTTP(payload),
        ),
        patch("app.services.user_sync.set_tenant_context", new=AsyncMock()),
        patch(
            "app.services.workspace_mcp_access.tenant_workspace_mcp_default",
            new=AsyncMock(side_effect=AssertionError("default lookup was unnecessary")),
        ),
    ):
        result = await getattr(service, f"sync_{provider}_users")(db, tenant_id)

    assert result["created"] == 0
    assert result["updated"] == 1
    assert existing.is_active is True
    assert db.execute_calls == 2  # one bulk user read and one sync-state update
    query = db.statements[0].compile()
    assert "users.tenant_id =" in str(query)
    assert uuid.UUID(tenant_id) in query.params.values()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "payload"),
    [
        (
            "microsoft",
            {
                "value": [
                    {"id": "ms-1", "mail": "one@test.com"},
                    {"id": "ms-2", "mail": "two@test.com"},
                    {"id": "ms-1", "mail": "ONE@test.com"},
                ]
            },
        ),
        (
            "google",
            {
                "users": [
                    {"id": "g-1", "primaryEmail": "one@test.com"},
                    {"id": "g-2", "primaryEmail": "two@test.com"},
                    {"id": "g-1", "primaryEmail": "ONE@test.com"},
                ]
            },
        ),
    ],
)
@pytest.mark.parametrize("enabled", [False, True])
async def test_sync_new_users_reads_directory_and_default_once(provider, payload, enabled):
    tenant_id = str(uuid.uuid4())
    db = _DB([])
    default = AsyncMock(return_value=enabled)

    with (
        patch("app.services.user_sync.get_fresh_token", new=AsyncMock(return_value="tok")),
        patch("app.services.user_sync.httpx.AsyncClient", return_value=_HTTP(payload)),
        patch("app.services.user_sync.set_tenant_context", new=AsyncMock()),
        patch(
            "app.services.workspace_mcp_access.tenant_workspace_mcp_default",
            new=default,
        ),
    ):
        result = await getattr(UserSyncService(), f"sync_{provider}_users")(db, tenant_id)

    assert result["created"] == 2
    assert result["updated"] == 1  # duplicate directory row reuses the new user
    assert default.await_count == 1
    assert len(db.added) == 2
    assert all(user.workspace_mcp_enabled is enabled for user in db.added)
    assert all(user.tenant_id == uuid.UUID(tenant_id) for user in db.added)
    assert db.execute_calls == 2
