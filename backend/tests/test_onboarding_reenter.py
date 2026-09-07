"""Onboarding re-entry route behavior."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers import onboarding


class _Result:
    def __init__(self, value): self.value = value
    def scalar_one_or_none(self): return self.value


class _Db:
    def __init__(self, tenant): self.tenant, self.added, self.commits = tenant, [], 0
    async def execute(self, _statement): return _Result(self.tenant)
    def add(self, value): self.added.append(value)
    async def commit(self): self.commits += 1


def _tenant():
    return SimpleNamespace(
        id="tenant", onboarding_completed=True, onboarding_step=4,
        cloud_root_folder={"google_drive": {"id": "existing-root"}},
    )


@pytest.mark.asyncio
async def test_reenter_admin_preserves_completion_and_root(monkeypatch):
    tenant = _tenant()
    db = _Db(tenant)
    admin = SimpleNamespace(tenant_id="tenant", id="admin")
    async def auth(*_args): return admin
    async def context(*_args): return None
    monkeypatch.setattr(onboarding, "require_admin", auth)
    monkeypatch.setattr(onboarding, "set_tenant_context", context)

    result = await onboarding.reenter_onboarding(
        onboarding.OnboardingReentryRequest(), None, db
    )

    assert result["cloud_root"] == tenant.cloud_root_folder
    assert tenant.onboarding_completed is True
    assert tenant.onboarding_step == 1
    assert db.commits == 1
    assert db.added[0].action == "onboarding_reentry"


@pytest.mark.asyncio
async def test_reenter_target_starts_migration(monkeypatch):
    tenant = _tenant()
    db = _Db(tenant)
    admin = SimpleNamespace(tenant_id="tenant", id="admin")
    async def auth(*_args): return admin
    async def context(*_args): return None
    class Migration:
        id = "migration-1"
    class MigrationService:
        async def start(self, *args, **kwargs): return Migration()
    monkeypatch.setattr(onboarding, "require_admin", auth)
    monkeypatch.setattr(onboarding, "set_tenant_context", context)
    monkeypatch.setattr(onboarding, "storage_migration", MigrationService(), raising=False)

    # The router imports the service inside the branch; patch the module object.
    import app.services.storage_migration as migration_module
    monkeypatch.setattr(migration_module, "storage_migration", MigrationService())
    result = await onboarding.reenter_onboarding(
        onboarding.OnboardingReentryRequest(target_provider="onedrive"), None, db
    )

    assert result["migration_id"] == "migration-1"
    assert tenant.onboarding_completed is True
    assert tenant.cloud_root_folder["google_drive"]["id"] == "existing-root"
    assert db.commits == 1


@pytest.mark.asyncio
async def test_reenter_migration_error_returns_conflict_without_commit(monkeypatch):
    tenant = _tenant()
    db = _Db(tenant)
    admin = SimpleNamespace(tenant_id="tenant", id="admin")
    async def auth(*_args): return admin
    async def context(*_args): return None
    class MigrationService:
        async def start(self, *args, **kwargs): raise ValueError("migration already active")
    monkeypatch.setattr(onboarding, "require_admin", auth)
    monkeypatch.setattr(onboarding, "set_tenant_context", context)
    import app.services.storage_migration as migration_module
    monkeypatch.setattr(migration_module, "storage_migration", MigrationService())

    with pytest.raises(HTTPException) as exc:
        await onboarding.reenter_onboarding(
            onboarding.OnboardingReentryRequest(target_provider="onedrive"), None, db
        )
    assert exc.value.status_code == 409
    assert db.commits == 0
