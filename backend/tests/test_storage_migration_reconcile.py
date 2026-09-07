from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest

from app.models.matter_document import MatterDocument
from app.models.plugin import Matter
from app.models.storage_migration import StorageMigration, StorageMigrationMatch
from app.models.tenant import Tenant
from app.services.storage_migration import StorageMigrationService


class _Rows:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        return self

    def all(self):
        return self.value if isinstance(self.value, list) else ([] if self.value is None else [self.value])


class _Db:
    def __init__(self, migration, tenant, matters, docs_by_matter):
        self.migration = migration
        self.tenant = tenant
        self.matters = matters
        self.docs_by_matter = docs_by_matter
        self.added = []
        self.flushes = 0

    async def execute(self, statement):
        entity = statement.column_descriptions[0].get("entity") if getattr(statement, "column_descriptions", None) else None
        if entity is StorageMigration:
            return _Rows(self.migration)
        if entity is Tenant:
            return _Rows(self.tenant)
        if entity is Matter:
            return _Rows(self.matters)
        if entity is MatterDocument:
            params = statement.compile().params
            matter_id = params.get("matter_id_1") or params.get("matter_id")
            return _Rows(self.docs_by_matter.get(str(matter_id), []))
        if entity is StorageMigrationMatch:
            return _Rows([])
        return _Rows([])

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        self.flushes += 1


def _migration(tenant_id):
    return SimpleNamespace(
        id=uuid.uuid4(), tenant_id=tenant_id, phase="planning",
        target_provider="google_drive", target_root={"id": "target-root", "path": "records"},
        source_provider="onedrive", evidence_version="old", bucket_counts={},
    )


def _matter(tenant_id, matter_id, name):
    return SimpleNamespace(
        id=matter_id, tenant_id=tenant_id, slug=name,
        cloud_folder={"onedrive": {"id": f"old-{matter_id}", "path": f"records/{name}"}},
    )


def _doc(tenant_id, matter_id, doc_id, sha="a" * 64):
    return SimpleNamespace(
        id=doc_id, tenant_id=tenant_id, matter_id=matter_id, storage_backend="onedrive",
        document_sha256=sha, filename="brief.pdf", file_size=12,
        provider_object_id=f"old-{doc_id}", provider_drive_id="old-drive",
        provider_parent_id="old-folder", provider_checksum=None,
        provider_etag=None, provider_version_id=None,
    )


@pytest.mark.asyncio
async def test_reconcile_uses_authoritative_root_and_persists_evidence_and_audit(monkeypatch):
    tenant_id = uuid.uuid4()
    matter_id = uuid.uuid4()
    migration = _migration(tenant_id)
    matter = _matter(tenant_id, matter_id, "alpha")
    doc = _doc(tenant_id, matter_id, uuid.uuid4())
    db = _Db(migration, SimpleNamespace(id=tenant_id), [matter], {str(matter_id): [doc]})
    discovery = AsyncMock()
    discovery.discover.return_value = [
        {"id": "folder", "name": "alpha", "path": "records/alpha", "is_folder": True,
         "marker": {"schema_version": 1, "tenant_id": str(tenant_id), "matter_id": str(matter_id)}},
        {"id": "file", "name": "brief.pdf", "parent_id": "folder", "is_folder": False,
         "sha256": doc.document_sha256, "size": 12},
    ]
    monkeypatch.setattr("app.services.storage_migration.set_tenant_context", AsyncMock())

    result = await StorageMigrationService(discovery).reconcile(db, str(migration.id))

    discovery.discover.assert_awaited_once_with(db, str(tenant_id), "google_drive", migration.target_root)
    assert result.phase == "awaiting_confirmation"
    assert result.evidence_version != "old"
    assert result.bucket_counts == {"matched": 2, "missing": 0, "ambiguous": 0}
    rows = [row for row in db.added if isinstance(row, StorageMigrationMatch)]
    assert {(row.object_type, row.bucket) for row in rows} == {("matter", "matched"), ("document", "matched")}
    assert next(row for row in rows if row.object_type == "matter").target_ref["id"] == "folder"
    assert db.flushes == 1


@pytest.mark.asyncio
async def test_reconcile_marks_shared_suffix_folder_ambiguous_and_duplicate_document_hash(monkeypatch):
    tenant_id = uuid.uuid4()
    first_id, second_id = uuid.uuid4(), uuid.uuid4()
    migration = _migration(tenant_id)
    first, second = _matter(tenant_id, first_id, "alpha"), _matter(tenant_id, second_id, "beta")
    # Both source folders intentionally share the same eight-hex suffix.
    first.id = second.id = uuid.UUID("12345678-aaaa-bbbb-cccc-123456789abc")
    first.slug, second.slug = "alpha", "beta"
    doc = _doc(tenant_id, first.id, uuid.uuid4())
    db = _Db(migration, SimpleNamespace(id=tenant_id), [first, second], {str(first.id): [doc], str(second.id): []})
    discovery = AsyncMock()
    discovery.discover.return_value = [
        {"id": "one", "name": "One (12345678)", "is_folder": True,
         "marker": {"schema_version": 1, "tenant_id": str(tenant_id), "matter_id": str(first.id)}},
        {"id": "two", "name": "Two (12345678)", "is_folder": True},
        {"id": "file-a", "name": "brief.pdf", "parent_id": "one", "is_folder": False, "sha256": doc.document_sha256, "size": 12},
        {"id": "file-b", "name": "brief.pdf", "parent_id": "one", "is_folder": False, "sha256": doc.document_sha256, "size": 12},
    ]
    monkeypatch.setattr("app.services.storage_migration.set_tenant_context", AsyncMock())

    result = await StorageMigrationService(discovery).reconcile(db, str(migration.id))

    assert result.bucket_counts["ambiguous"] >= 2
    assert all(row.bucket == "ambiguous" for row in db.added if isinstance(row, StorageMigrationMatch))


@pytest.mark.asyncio
async def test_reconcile_revalidates_target_after_discovery(monkeypatch):
    tenant_id = uuid.uuid4()
    migration = _migration(tenant_id)
    db = _Db(migration, SimpleNamespace(id=tenant_id), [], {})
    discovery = AsyncMock()

    async def discover(*_args):
        migration.target_root = {"id": "changed-root"}
        return []

    discovery.discover.side_effect = discover
    monkeypatch.setattr("app.services.storage_migration.set_tenant_context", AsyncMock())

    with pytest.raises(ValueError, match="target changed"):
        await StorageMigrationService(discovery).reconcile(db, str(migration.id))
