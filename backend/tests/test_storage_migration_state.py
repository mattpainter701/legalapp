"""State-machine guards for provider migration."""

from types import SimpleNamespace

import pytest

from app.services.storage_migration import StorageMigrationService, assert_provider_change_allowed


class _Rows:
    def __init__(self, value=None): self.value = value
    def scalar_one_or_none(self): return self.value
    def scalars(self): return self
    def first(self): return self.value
    def all(self): return self.value or []


class _StartDb:
    def __init__(self):
        self.calls = 0
        self.added = []

    async def execute(self, _statement):
        self.calls += 1
        if self.calls == 1:
            return _Rows(SimpleNamespace(cloud_root_folder={"onedrive": {"id": "target"}}, updated_at=None))
        if self.calls == 2:
            return _Rows(SimpleNamespace(primary_cloud_provider="google_drive", updated_at=None))
        return _Rows(None)

    def add(self, row): self.added.append(row)
    async def flush(self): return None


@pytest.mark.asyncio
async def test_start_issues_server_evidence_and_rejects_unbound_root():
    db = _StartDb()
    row = await StorageMigrationService().start(db, "tenant", "onedrive", evidence_version="client", target_root={"id": "target"})
    assert row.evidence_version != "client"
    assert row.phase == "planning"


@pytest.mark.asyncio
async def test_provider_guard_rejects_target_bypass():
    class Db:
        async def execute(self, _statement):
            return _Rows(SimpleNamespace(target_provider="onedrive"))

    with pytest.raises(ValueError, match="active storage migration"):
        await assert_provider_change_allowed(Db(), "tenant", "onedrive")
