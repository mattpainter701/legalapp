"""Onboarding re-entry must preserve and audit an existing cloud root."""

from types import SimpleNamespace

import pytest

from app.routers import onboarding


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _Db:
    def __init__(self, tenant):
        self.tenant = tenant
        self.added = []

    async def execute(self, _statement):
        return _Result(self.tenant)

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        return None


@pytest.mark.asyncio
async def test_complete_onboarding_preserves_existing_root(monkeypatch):
    tenant_id = "12345678-1234-1234-1234-123456789abc"
    root = {"google_drive": {"id": "existing-root"}}
    tenant = SimpleNamespace(
        id=tenant_id, onboarding_completed=True, onboarding_step=4,
        cloud_root_folder=root,
    )
    user = SimpleNamespace(tenant_id=tenant_id, id="operator-1")
    db = _Db(tenant)

    async def current_user(_request, _db):
        return user

    async def no_context(*_args):
        return None

    async def agreements(*_args):
        return {"blocking": False}

    async def integrations(*_args):
        return {"google": SimpleNamespace(connected=True), "microsoft": SimpleNamespace(connected=False)}

    monkeypatch.setattr(onboarding, "get_current_user", current_user)
    monkeypatch.setattr(onboarding, "set_tenant_context", no_context)
    monkeypatch.setattr(onboarding, "agreement_status", agreements)
    monkeypatch.setattr(onboarding, "_get_integration_status", integrations)

    response = await onboarding.complete_onboarding(None, db)

    assert response.cloud_root == root
    assert tenant.cloud_root_folder == root
    assert len(db.added) == 1
    assert db.added[0].action == "onboarding_rerun"
