"""Pure reconciliation guard tests for cloud-provider migration."""

from types import SimpleNamespace

import httpx
import pytest

from app.services.storage_migration import StorageMigrationService
from app.services.storage_discovery import StorageDiscovery


def _matter(**kwargs):
    values = {"id": "12345678-aaaa-bbbb-cccc-123456789abc", "slug": "acme-v-acme"}
    values.update(kwargs)
    return SimpleNamespace(**values)


def test_marker_wins_over_name_and_path():
    matter = _matter(cloud_folder={"path": "claritylegal-records/acme-v-acme"})
    items = [
        {"id": "renamed", "name": "Renamed matter", "is_folder": True,
         "marker": {"schema_version": 1, "tenant_id": "tenant-1", "matter_id": matter.id}},
        {"id": "suffix", "name": "Other (12345678)", "is_folder": True},
    ]
    matches, rung = StorageMigrationService._match_matter(matter, items, "tenant-1")
    assert rung == "marker"
    assert [item["id"] for item in matches] == ["renamed"]


def test_duplicate_id_suffix_is_ambiguous():
    matter = _matter(cloud_folder={})
    items = [
        {"id": "one", "name": "A (12345678)", "is_folder": True},
        {"id": "two", "name": "B (12345678)", "is_folder": True},
    ]
    matches, rung = StorageMigrationService._match_matter(matter, items, "tenant-1")
    assert rung == "id_suffix"
    assert len(matches) == 2


def test_canonical_path_is_fallback():
    matter = _matter(cloud_folder={"path": "claritylegal-records/acme-v-acme"})
    items = [{"id": "path", "name": "acme-v-acme", "path": "claritylegal-records/acme-v-acme", "is_folder": True}]
    matches, rung = StorageMigrationService._match_matter(matter, items, "tenant-1")
    assert rung == "canonical_path"
    assert matches[0]["id"] == "path"


def test_foreign_or_invalid_marker_cannot_fall_back_to_suffix():
    matter = _matter(cloud_folder={})
    items = [
        {"id": "foreign", "name": "Foreign (12345678)", "is_folder": True,
         "marker": {"schema_version": 1, "tenant_id": "other", "matter_id": matter.id}},
        {"id": "invalid", "name": "Invalid (12345678)", "is_folder": True,
         "marker": {"schema_version": 2, "tenant_id": "tenant-1", "matter_id": matter.id}},
    ]
    matches, rung = StorageMigrationService._match_matter(matter, items, "tenant-1")
    assert matches == []
    assert rung is None


def test_duplicate_target_suffixes_are_ambiguous():
    matter = _matter()
    items = [
        {"id": "one", "name": "First (12345678)", "is_folder": True},
        {"id": "two", "name": "Second (12345678)", "is_folder": True},
    ]
    matches, rung = StorageMigrationService._match_matter(matter, items, "tenant-1")
    assert rung == "id_suffix"
    assert {item["id"] for item in matches} == {"one", "two"}


def test_filename_size_fallback_is_per_candidate_when_sibling_has_hash():
    class Rows:
        def scalars(self): return self
        def all(self): return [
            SimpleNamespace(id="doc-1", storage_backend="google_drive", document_sha256=None,
                            provider_checksum=None, provider_object_id="old", provider_drive_id=None,
                            provider_parent_id=None, provider_etag=None, provider_version_id=None,
                            filename="brief.pdf", file_size=12),
        ]

    class Db:
        def __init__(self): self.added = []
        async def execute(self, _statement): return Rows()
        def add(self, row): self.added.append(row)

    async def run():
        db = Db()
        migration = SimpleNamespace(id="mig", tenant_id="tenant", source_provider="google_drive")
        matter = SimpleNamespace(id="matter")
        items = [
            {"id": "hashed-sibling", "name": "other.pdf", "size": 99, "sha256": "a" * 64,
             "parent_id": "folder"},
            {"id": "target", "name": "brief.pdf", "size": 12, "parent_id": "folder"},
        ]
        counts = {"matched": 0, "missing": 0, "ambiguous": 0}
        await StorageMigrationService()._reconcile_documents(db, migration, matter,
            {"id": "folder"}, items, counts)
        assert counts == {"matched": 1, "missing": 0, "ambiguous": 0}
        assert db.added[0].target_ref["id"] == "target"

    import asyncio
    asyncio.run(run())


@pytest.mark.asyncio
async def test_google_discovery_paginates_and_attaches_marker(monkeypatch):
    calls = []

    async def token(*_args):
        return "token"

    async def handler(request):
        calls.append(str(request.url))
        if request.url.path.endswith("/files/root"):
            return httpx.Response(200, json={"id": "root", "mimeType": "application/vnd.google-apps.folder"})
        if request.url.path.endswith("/files/marker"):
            return httpx.Response(200, text='{"tenant_id":"t1","matter_id":"m1","schema_version":1}')
        if request.url.params.get("pageToken") == "next":
            return httpx.Response(200, json={"files": [{"id": "marker", "name": ".lawhand-matter.json", "mimeType": "text/plain", "parents": ["folder"]}]})
        if "folder" in request.url.params.get("q", ""):
            return httpx.Response(200, json={"files": []})
        return httpx.Response(200, json={"nextPageToken": "next", "files": [{"id": "folder", "name": "Matter (12345678)", "mimeType": "application/vnd.google-apps.folder", "parents": ["root"]}]})

    monkeypatch.setattr("app.services.storage_discovery.get_fresh_token", token)
    transport = httpx.MockTransport(handler)
    client_type = httpx.AsyncClient
    monkeypatch.setattr("app.services.storage_discovery.httpx.AsyncClient", lambda **kwargs: client_type(transport=transport, **{k: v for k, v in kwargs.items() if k != "transport"}))
    result = await StorageDiscovery()._google(None, "tenant", {"id": "root"})
    folder = next(item for item in result if item["id"] == "folder")
    assert folder["marker"]["matter_id"] == "m1"
    assert any("pageToken=next" in call for call in calls)
