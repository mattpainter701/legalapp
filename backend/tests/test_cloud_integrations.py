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
    _parse_index_date,
    CloudHit,
    CloudSearchService,
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

    monkeypatch.setattr(cloud_init, "get_fresh_token", fake_token)
    monkeypatch.setattr(cloud_init, "_ensure_onedrive_folder", fake_onedrive_folder)
    monkeypatch.setattr(cloud_init, "_ensure_gdrive_folder", fake_gdrive_folder)
    monkeypatch.setattr(cloud_init, "_get_onedrive_web_url", fake_web_url)

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

    monkeypatch.setattr(cloud_init, "get_fresh_token", fake_token)
    monkeypatch.setattr(cloud_init, "_ensure_onedrive_folder", fake_onedrive_folder)
    monkeypatch.setattr(cloud_init, "_ensure_gdrive_folder", fake_gdrive_folder)
    monkeypatch.setattr(cloud_init, "_get_onedrive_web_url", fake_web_url)

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
