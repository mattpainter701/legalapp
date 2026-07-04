"""Regression tests for the cloud-integration review fixes.

These cover the pure logic and wiring that don't require a live database:
  - the cloud-sync scheduled agent is registered and manually triggerable
  - the metadata-index source mapping / date parsing helpers behave
  - the Gmail live-search query no longer emits invalid ``{}`` syntax
"""

import pytest

from app.services import cloud_search
from app.services.cloud_search import (
    _INDEX_SOURCE_MAP,
    _cloud_metadata_scope_folder_ids,
    _parse_index_date,
    CloudHit,
    CloudSearchService,
)
from app.services.cloud_sync import (
    _matter_folder_ids,
    _sharepoint_folder_refs,
    CloudSyncService,
)
from app.services.rag import build_cloud_context
from app.services.scheduler import AGENT_REGISTRY, LegalScheduler


def test_cloud_sync_agent_registered():
    """cloud-sync must appear in the registry and the manual-trigger map."""
    names = {a["name"] for a in AGENT_REGISTRY}
    assert "cloud-sync" in names

    # run_agent_manually exposes a closed-over agent_map; an unknown name is
    # rejected, a known one is accepted (we don't execute it here).
    sched = LegalScheduler()
    assert hasattr(sched, "run_cloud_sync")


def test_index_source_map_targets_valid_fetch_sources():
    """Every (provider, object_type) maps to a source fetch_content understands."""
    valid_sources = {"drive", "gmail", "onedrive", "sharepoint", "outlook"}
    assert set(_INDEX_SOURCE_MAP.keys()) == {
        ("google", "file"),
        ("google", "email"),
        ("microsoft", "file"),
        ("microsoft", "email"),
    }
    assert all(v in valid_sources for v in _INDEX_SOURCE_MAP.values())


def test_parse_index_date():
    assert _parse_index_date("2026-01-01") is not None
    assert _parse_index_date("2026-01-01T12:30:00") is not None
    assert _parse_index_date("not-a-date") is None
    assert _parse_index_date("") is None


def test_parse_index_date_is_timezone_aware():
    parsed = _parse_index_date("2026-01-01")
    assert parsed is not None and parsed.tzinfo is not None


def test_gmail_query_helpers_present():
    """Guard against re-introducing the curly-brace Gmail query bug.

    The source of _search_gmail must use bare from:/to: operators, never the
    invalid ``from:{...}`` brace form.
    """
    import inspect

    src = inspect.getsource(cloud_search.CloudSearchService._search_gmail)
    assert "from:{sanitised}" in src
    assert "from:{{{sanitised}}}" not in src


@pytest.mark.asyncio
async def test_cloud_search_dispatches_subsource_plans(monkeypatch):
    """Planner emits sub-sources (gmail/outlook), not provider names."""
    service = CloudSearchService()
    calls: list[str] = []

    async def fake_gmail(*args, **kwargs):
        calls.append("gmail")
        return []

    async def fake_graph(*args, **kwargs):
        calls.append("graph")
        return []

    async def fake_index(*args, **kwargs):
        calls.append("index")
        return []

    monkeypatch.setattr(service, "_search_gmail", fake_gmail)
    monkeypatch.setattr(service, "_search_google_drive", fake_gmail)
    monkeypatch.setattr(service, "_search_graph", fake_graph)
    monkeypatch.setattr(service, "search_index", fake_index)

    await service.search(
        db=None,
        plan={"sources": ["gmail", "outlook"], "keywords": ["acme"]},
        tenant_id="tenant-1",
        user_id="user-1",
    )

    assert calls == ["gmail", "graph", "index"]


@pytest.mark.asyncio
async def test_cloud_search_scopes_to_primary_and_context_folders(monkeypatch):
    service = CloudSearchService()
    drive_folder_ids: list[str | None] = []
    graph_folder_ids: list[str | None] = []

    async def fake_drive(*args, folder_id=None, **kwargs):
        drive_folder_ids.append(folder_id)
        return []

    async def fake_graph(*args, folder_id=None, **kwargs):
        graph_folder_ids.append(folder_id)
        return []

    async def fake_index(*args, **kwargs):
        return []

    monkeypatch.setattr(service, "_search_google_drive", fake_drive)
    monkeypatch.setattr(service, "_search_graph", fake_graph)
    monkeypatch.setattr(service, "search_index", fake_index)

    await service.search(
        db=None,
        plan={"sources": ["drive", "onedrive"], "keywords": ["acme"]},
        tenant_id="tenant-1",
        user_id="user-1",
        matter_cloud_folder={
            "google_drive": {"matter_folder_id": "gd-primary"},
            "onedrive": {"matter_folder_id": "od-primary"},
            "context_folders": [
                {
                    "provider": "google_drive",
                    "matter_folder_id": "gd-context",
                    "id": "local-ui-id",
                },
                {"provider": "onedrive", "matter_folder_id": "od-context"},
            ],
        },
    )

    assert drive_folder_ids == ["gd-primary", "gd-context"]
    assert graph_folder_ids == ["od-primary", "od-context"]


@pytest.mark.asyncio
async def test_build_cloud_context_accepts_cloud_hit_objects():
    hit = CloudHit(
        provider="google",
        source="gmail",
        object_id="msg-1",
        title="Acme settlement email",
        snippet="Fallback snippet",
        url="https://mail.google.com/#all/msg-1",
        modified_time="2026-06-01T12:00:00Z",
        mime_type="message/rfc822",
        relevance_score=0.91,
    )

    context = await build_cloud_context([{"hit": hit, "content": "Email body text"}])

    assert "google/gmail: Acme settlement email" in context
    assert "Email body text" in context
    assert "https://mail.google.com/#all/msg-1" in context


def test_source_enabled_accepts_provider_aliases():
    assert cloud_search._source_enabled(["google"], "gmail")
    assert cloud_search._source_enabled(["google"], "drive")
    assert cloud_search._source_enabled(["microsoft"], "outlook")
    assert cloud_search._source_enabled(["microsoft"], "sharepoint")
    assert not cloud_search._source_enabled(["google"], "outlook")


def test_cloud_metadata_scope_folder_ids_extracts_all_matter_folders():
    assert _cloud_metadata_scope_folder_ids(
        {
            "google_drive": {"matter_folder_id": "gd-primary"},
            "onedrive": {"matter_folder_id": "od-primary"},
            "sharepoint": {
                "matter_folder_id": "sp-primary",
                "drive_id": "drive-1",
            },
            "context_folders": [
                {"provider": "google_drive", "matter_folder_id": "gd-context"},
                {"provider": "onedrive", "matter_folder_id": "od-context"},
                {
                    "provider": "sharepoint",
                    "matter_folder_id": "sp-context",
                    "drive_id": "drive-1",
                },
            ],
        }
    ) == [
        "gd-primary",
        "gd-context",
        "od-primary",
        "od-context",
        "sp-primary",
        "sp-context",
    ]


def test_matter_scoped_sync_extracts_primary_subfolders_and_context():
    cloud_folder = {
        "google_drive": {
            "matter_folder_id": "gd-primary",
            "subfolders": {
                "documents": "gd-docs",
                "pleadings": "gd-pleadings",
            },
        },
        "onedrive": {
            "id": "od-primary",
            "subfolders": {
                "documents": "od-docs",
            },
        },
        "context_folders": [
            {"provider": "google_drive", "matter_folder_id": "gd-context"},
            {"provider": "onedrive", "folder_id": "od-context"},
        ],
    }

    assert _matter_folder_ids(cloud_folder, "google_drive") == [
        "gd-primary",
        "gd-docs",
        "gd-pleadings",
        "gd-context",
    ]
    assert _matter_folder_ids(cloud_folder, "onedrive") == [
        "od-primary",
        "od-docs",
        "od-context",
    ]


def test_matter_scoped_sync_extracts_sharepoint_drive_refs():
    cloud_folder = {
        "sharepoint": {
            "drive_id": "drive-1",
            "matter_folder_id": "sp-primary",
            "subfolders": {
                "documents": "sp-docs",
            },
        },
        "context_folders": [
            {
                "provider": "sharepoint",
                "drive_id": "drive-2",
                "matter_folder_id": "sp-context",
            }
        ],
    }

    assert _sharepoint_folder_refs(cloud_folder) == [
        ("drive-1", "sp-primary"),
        ("drive-1", "sp-docs"),
        ("drive-2", "sp-context"),
    ]


@pytest.mark.asyncio
async def test_sync_matter_folders_dispatches_only_folder_syncs(monkeypatch):
    service = CloudSyncService()
    calls: list[tuple[str, object]] = []
    tenant_id = "11111111-1111-1111-1111-111111111111"

    class FakeDb:
        async def execute(self, *args, **kwargs):
            return None

    async def fake_google(db, tenant_id, folder_ids, user_id=None):
        calls.append(("google", folder_ids))
        assert user_id == "user-1"
        return 2

    async def fake_onedrive(db, tenant_id, folder_ids, user_id=None):
        calls.append(("onedrive", folder_ids))
        assert user_id == "user-1"
        return 3

    async def fake_sharepoint(db, tenant_id, folder_refs):
        calls.append(("sharepoint", folder_refs))
        return 4

    async def fail_sync_all(*args, **kwargs):
        raise AssertionError("matter folder sync must not call sync_all")

    monkeypatch.setattr(service, "sync_google_drive_folders", fake_google)
    monkeypatch.setattr(service, "sync_onedrive_folders", fake_onedrive)
    monkeypatch.setattr(service, "sync_sharepoint_folders", fake_sharepoint)
    monkeypatch.setattr(service, "sync_all", fail_sync_all)

    result = await service.sync_matter_folders(
        db=FakeDb(),
        tenant_id=tenant_id,
        user_id="user-1",
        matter_cloud_folder={
            "google_drive": {"matter_folder_id": "gd-primary"},
            "onedrive": {"matter_folder_id": "od-primary"},
            "sharepoint": {
                "drive_id": "drive-1",
                "matter_folder_id": "sp-primary",
            },
        },
    )

    assert result == {
        "google": {"files": 2, "emails": 0},
        "microsoft": {"files": 7, "emails": 0},
    }
    assert calls == [
        ("google", ["gd-primary"]),
        ("onedrive", ["od-primary"]),
        ("sharepoint", [("drive-1", "sp-primary")]),
    ]


@pytest.mark.asyncio
async def test_cloud_root_provisions_both_connected_providers(monkeypatch):
    """A tenant can have Microsoft 365 and Google connected simultaneously."""
    from app.services import cloud_init

    async def fake_token(_db, _tenant_id, provider):
        return {"microsoft": "ms-token", "google": "g-token"}.get(provider)

    async def fake_onedrive_folder(_token, name, parent_id):
        return f"od:{parent_id}:{name}"

    async def fake_gdrive_folder(_token, name, parent_id):
        return f"gd:{parent_id}:{name}"

    async def fake_web_url(_token, folder_id):
        return f"https://onedrive/{folder_id}"

    async def fake_onedrive_metadata(_token, folder_id):
        return {
            "id": folder_id,
            "name": folder_id.split(":")[-1],
            "webUrl": f"https://onedrive/{folder_id}",
            "folder": {},
        }

    async def fake_gdrive_metadata(_token, folder_id):
        return {
            "id": folder_id,
            "name": folder_id.split(":")[-1],
            "webViewLink": f"https://drive/{folder_id}",
            "mimeType": "application/vnd.google-apps.folder",
        }

    monkeypatch.setattr(cloud_init, "get_fresh_token", fake_token)
    monkeypatch.setattr(cloud_init, "_ensure_onedrive_folder", fake_onedrive_folder)
    monkeypatch.setattr(cloud_init, "_ensure_gdrive_folder", fake_gdrive_folder)
    monkeypatch.setattr(cloud_init, "_get_onedrive_web_url", fake_web_url)
    monkeypatch.setattr(cloud_init, "_get_onedrive_folder_metadata", fake_onedrive_metadata)
    monkeypatch.setattr(cloud_init, "_get_gdrive_folder_metadata", fake_gdrive_metadata)

    root = await cloud_init.initialize_cloud_root_folder(None, "tenant-1")

    assert root["path"] == "claritylegal-records"
    assert root["subfolders"] == [
        "emails",
        "documents",
        "pleadings",
        "correspondence",
        "billing",
    ]
    assert "onedrive" in root
    assert "google_drive" in root
    assert root["onedrive"]["id"] == "od:root:claritylegal-records"
    assert root["google_drive"]["id"] == "gd:root:claritylegal-records"


@pytest.mark.asyncio
async def test_matter_folder_metadata_uses_canonical_layout(monkeypatch):
    """Matter metadata records the platform-created canonical storage paths."""
    from app.services import cloud_init

    async def fake_token(_db, _tenant_id, provider):
        return {"microsoft": "ms-token", "google": "g-token"}.get(provider)

    async def fake_onedrive_folder(_token, name, parent_id):
        return f"od:{parent_id}:{name}"

    async def fake_gdrive_folder(_token, name, parent_id):
        return f"gd:{parent_id}:{name}"

    async def fake_web_url(_token, folder_id):
        return f"https://onedrive/{folder_id}"

    async def fake_onedrive_metadata(_token, folder_id):
        return {
            "id": folder_id,
            "name": folder_id.split(":")[-1],
            "webUrl": f"https://onedrive/{folder_id}",
            "folder": {},
        }

    async def fake_gdrive_metadata(_token, folder_id):
        return {
            "id": folder_id,
            "name": folder_id.split(":")[-1],
            "webViewLink": f"https://drive/{folder_id}",
            "mimeType": "application/vnd.google-apps.folder",
        }

    monkeypatch.setattr(cloud_init, "get_fresh_token", fake_token)
    monkeypatch.setattr(cloud_init, "_ensure_onedrive_folder", fake_onedrive_folder)
    monkeypatch.setattr(cloud_init, "_ensure_gdrive_folder", fake_gdrive_folder)
    monkeypatch.setattr(cloud_init, "_get_onedrive_web_url", fake_web_url)
    monkeypatch.setattr(cloud_init, "_get_onedrive_folder_metadata", fake_onedrive_metadata)
    monkeypatch.setattr(cloud_init, "_get_gdrive_folder_metadata", fake_gdrive_metadata)

    metadata = await cloud_init.initialize_matter_folders(
        None,
        "tenant-1",
        "acme-v-smith",
        {
            "onedrive": {"matters_folder_id": "od-matters"},
            "google_drive": {"matters_folder_id": "gd-matters"},
        },
    )

    assert metadata["path"] == "claritylegal-records/acme-v-smith"
    assert metadata["subfolder_paths"] == {
        "emails": "claritylegal-records/acme-v-smith/emails",
        "documents": "claritylegal-records/acme-v-smith/documents",
        "pleadings": "claritylegal-records/acme-v-smith/pleadings",
        "correspondence": "claritylegal-records/acme-v-smith/correspondence",
        "billing": "claritylegal-records/acme-v-smith/billing",
    }
    assert set(metadata["onedrive"]["subfolders"]) == set(metadata["subfolder_paths"])
    assert set(metadata["google_drive"]["subfolders"]) == set(metadata["subfolder_paths"])


def test_cloud_folder_selection_prefers_existing_canonical_before_duplicates():
    """Re-auth must reconnect to existing roots instead of creating another numbered folder."""
    from app.services import cloud_init

    items = [
        {"id": "folder-6", "name": "claritylegal-records 6", "createdDateTime": "2026-06-15T10:00:00Z"},
        {"id": "folder-2", "name": "claritylegal-records 2", "createdDateTime": "2026-06-10T10:00:00Z"},
        {"id": "folder-root", "name": "claritylegal-records", "createdDateTime": "2026-06-14T10:00:00Z"},
    ]

    chosen = cloud_init._choose_existing_folder(items, "claritylegal-records")

    assert chosen["id"] == "folder-root"


def test_cloud_folder_selection_falls_back_to_lowest_duplicate_suffix():
    from app.services import cloud_init

    items = [
        {"id": "folder-6", "name": "claritylegal-records 6", "createdDateTime": "2026-06-15T10:00:00Z"},
        {"id": "folder-2", "name": "claritylegal-records 2", "createdDateTime": "2026-06-10T10:00:00Z"},
    ]

    chosen = cloud_init._choose_existing_folder(items, "claritylegal-records")

    assert chosen["id"] == "folder-2"


@pytest.mark.asyncio
async def test_gdrive_folder_create_409_recovers_existing_folder(monkeypatch):
    """If another worker creates the folder after our pre-list, re-list and reuse it."""
    from app.services import cloud_init

    class Response:
        def __init__(self, status_code, payload=None, text=""):
            self.status_code = status_code
            self._payload = payload or {}
            self.text = text

        def json(self):
            return self._payload

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.get_calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, *args, **kwargs):
            self.get_calls += 1
            if self.get_calls == 1:
                return Response(200, {"files": []})
            return Response(
                200,
                {
                    "files": [
                        {
                            "id": "folder-existing",
                            "name": "Matter A",
                            "createdTime": "2026-07-02T10:00:00Z",
                        }
                    ]
                },
            )

        async def post(self, *args, **kwargs):
            return Response(409, text="already exists")

    monkeypatch.setattr(cloud_init.httpx, "AsyncClient", FakeClient)

    assert await cloud_init._ensure_gdrive_folder(
        "token", "Matter A", "parent-folder"
    ) == "folder-existing"
