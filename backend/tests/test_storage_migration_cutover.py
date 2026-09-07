"""Cutover safety invariants."""

from types import SimpleNamespace

import pytest

from app.services.storage_migration import StorageMigrationService


class _Rows:
    def __init__(self, value): self.value = value
    def scalar_one_or_none(self): return self.value


@pytest.mark.asyncio
async def test_cutover_rejects_unresolved_inventory_before_rebinding():
    migration = SimpleNamespace(
        phase="awaiting_confirmation", tenant_id="tenant", evidence_version="evidence",
        bucket_counts={"matched": 1, "missing": 1, "ambiguous": 0},
    )

    class Db:
        async def execute(self, statement):
            # First statement locks the migration; the second locks tenant.
            entity = statement.column_descriptions[0].get("entity")
            if entity and entity.__name__ == "StorageMigration":
                return _Rows(migration)
            return _Rows(SimpleNamespace(id="tenant"))

    with pytest.raises(ValueError, match="every matter and document"):
        await StorageMigrationService().cutover(Db(), "migration", evidence_version="evidence")


@pytest.mark.asyncio
async def test_successful_cutover_rebinds_document_matter_root_and_reindex():
    from app.models.cloud_metadata import CloudMetadata
    from app.models.matter_document import MatterDocument
    from app.models.plugin import Matter
    from app.models.storage_migration import StorageMigration, StorageMigrationMatch
    from app.models.tenant import Tenant, TenantSettings

    migration = SimpleNamespace(
        id="migration", phase="awaiting_confirmation", tenant_id="tenant",
        source_provider="google_drive", target_provider="onedrive", evidence_version="evidence",
        bucket_counts={"matched": 2, "missing": 0, "ambiguous": 0}, previous_root={"google_drive": {"id": "old-root"}},
        operator_id="operator", target_root={"id": "new-root", "path": "records"},
    )
    tenant = SimpleNamespace(id="tenant", cloud_root_folder={"google_drive": {"id": "old-root"}})
    settings = SimpleNamespace(primary_cloud_provider="google_drive")
    matter = SimpleNamespace(id="matter", cloud_folder={"google_drive": {"matter_folder_id": "old-folder"}})
    doc = SimpleNamespace(
        id="doc", storage_backend="google_drive", storage_provider="google",
        provider_object_id="old-doc", provider_drive_id="old-drive", provider_parent_id="old-folder",
        provider_checksum=None, provider_etag="old-etag", provider_version_id="old-version",
        document_sha256="a" * 64, filename="brief.pdf", file_size=12, storage_path="old-url",
        _storage_backend="google_drive",
    )
    matter_row = SimpleNamespace(object_type="matter", object_id="matter", bucket="matched",
        source_ref={"matter_folder_id": "old-folder"}, target_ref={"id": "new-folder", "matter_folder_id": "new-folder"})
    doc_row = SimpleNamespace(object_type="document", object_id="doc", bucket="matched",
        source_ref={"provider_object_id": "old-doc", "provider_drive_id": "old-drive", "provider_parent_id": "old-folder",
                    "provider_checksum": None, "provider_etag": "old-etag", "provider_version_id": "old-version",
                    "document_sha256": "a" * 64, "filename": "brief.pdf", "size": 12, "backend": "google_drive"},
        target_ref={"id": "new-doc", "drive_id": "new-drive", "parent_id": "new-folder", "sha256": "b" * 64,
                    "etag": "new-etag", "version_id": "new-version", "url": "new-url"})

    class Rows:
        def __init__(self, value=None): self.value = value
        def scalar_one_or_none(self): return self.value
        def scalars(self): return self
        def all(self): return self.value if isinstance(self.value, list) else ([] if self.value is None else [self.value])

    class Db:
        def __init__(self): self.added = []; self.deleted = False
        async def execute(self, statement):
            entity = statement.column_descriptions[0].get("entity") if getattr(statement, "column_descriptions", None) else None
            if entity is StorageMigration: return Rows(migration)
            if entity is Tenant: return Rows(tenant)
            if entity is TenantSettings: return Rows(settings)
            if entity is StorageMigrationMatch: return Rows([matter_row, doc_row])
            if entity is MatterDocument: return Rows([doc])
            if entity is Matter: return Rows([matter])
            self.deleted = True
            return Rows([])
        async def get(self, model, object_id):
            if model is MatterDocument: return doc
            if model is Matter: return matter
            return None
        def add(self, value): self.added.append(value)
        async def flush(self): return None

    db = Db()
    result = await StorageMigrationService().cutover(db, "migration", operator_id="operator", evidence_version="evidence")
    assert result.phase == "complete"
    assert result.needs_reindex is True
    assert doc.storage_provider == "microsoft"
    assert doc._storage_backend == "onedrive"
    assert doc.provider_object_id == "new-doc"
    assert doc.provider_checksum == "b" * 64
    assert matter.cloud_folder["onedrive"]["matter_folder_id"] == "new-folder"
    assert tenant.cloud_root_folder["onedrive"]["id"] == "new-root"
    assert db.deleted is True


@pytest.mark.asyncio
async def test_cutover_rejects_stale_evidence_before_mutation():
    migration = SimpleNamespace(
        phase="awaiting_confirmation", tenant_id="tenant", evidence_version="server-evidence",
        bucket_counts={"matched": 0, "missing": 0, "ambiguous": 0},
    )

    class Rows:
        def scalar_one_or_none(self): return migration

    class Db:
        async def execute(self, statement):
            # The first statement is the non-mutating migration lock query.
            return Rows()

    with pytest.raises(ValueError, match="evidence changed"):
        await StorageMigrationService().cutover(Db(), "migration", evidence_version="stale")


@pytest.mark.asyncio
async def test_cutover_rejects_changed_source_pointer():
    from app.models.matter_document import MatterDocument
    from app.models.storage_migration import StorageMigration, StorageMigrationMatch
    from app.models.tenant import Tenant

    migration = SimpleNamespace(
        id="migration", phase="awaiting_confirmation", tenant_id="tenant",
        source_provider="google_drive", target_provider="onedrive", evidence_version="evidence",
        bucket_counts={"matched": 1, "missing": 0, "ambiguous": 0},
    )
    tenant = SimpleNamespace(id="tenant")
    doc = SimpleNamespace(id="doc", storage_backend="google_drive", provider_object_id="changed",
        provider_drive_id=None, provider_parent_id=None, provider_checksum=None,
        provider_etag=None, provider_version_id=None, document_sha256=None,
        filename="brief.pdf", file_size=12)
    doc_row = SimpleNamespace(object_type="document", object_id="doc", bucket="matched",
        source_ref={"provider_object_id": "original"}, target_ref={"id": "new-doc"})

    class Rows:
        def __init__(self, value): self.value = value
        def scalar_one_or_none(self): return self.value
        def scalars(self): return self
        def all(self): return self.value if isinstance(self.value, list) else [self.value]

    class Db:
        async def execute(self, statement):
            entity = statement.column_descriptions[0].get("entity") if getattr(statement, "column_descriptions", None) else None
            if entity is StorageMigration: return Rows(migration)
            if entity is Tenant: return Rows(tenant)
            if entity is StorageMigrationMatch: return Rows([doc_row])
            if entity is MatterDocument: return Rows([doc])
            return Rows([])

    with pytest.raises(ValueError, match="source document pointer changed"):
        await StorageMigrationService().cutover(Db(), "migration", evidence_version="evidence")
