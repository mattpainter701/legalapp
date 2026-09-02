from __future__ import annotations

import pytest

from app.middleware import access_log


class _FakeSession:
    def __init__(self, events: list[object]):
        self.events = events

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None

    def add(self, row):
        self.events.append(("add", row.tenant_id, row.request_id))

    async def execute(self, *args, **kwargs):
        return None

    async def commit(self):
        self.events.append("commit")


@pytest.mark.asyncio
async def test_access_log_establishes_rls_context_before_insert(monkeypatch):
    events: list[object] = []

    async def fake_set_tenant_context(db, tenant_id):
        assert isinstance(db, _FakeSession)
        events.append(("tenant", tenant_id))

    monkeypatch.setattr(access_log, "async_session_maker", lambda: _FakeSession(events))
    monkeypatch.setattr(access_log, "set_tenant_context", fake_set_tenant_context)

    await access_log._write_log(
        tenant_id="00000000-0000-4000-8000-000000000001",
        user_id=None,
        endpoint="/api/auth/me",
        method="GET",
        status_code=200,
        latency_ms=1.5,
        ip_address="127.0.0.1",
        user_agent_short="test",
        request_id="request-123",
    )

    assert events == [
        ("tenant", "00000000-0000-4000-8000-000000000001"),
        (
            "add",
            "00000000-0000-4000-8000-000000000001",
            "request-123",
        ),
        "commit",
    ]
