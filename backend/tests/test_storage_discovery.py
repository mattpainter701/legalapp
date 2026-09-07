import httpx
import pytest

from app.services.storage_discovery import StorageDiscovery


def _client(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient
    monkeypatch.setattr(
        "app.services.storage_discovery.httpx.AsyncClient",
        lambda **kwargs: original(transport=transport, **{k: v for k, v in kwargs.items() if k != "transport"}),
    )


@pytest.mark.asyncio
async def test_google_requires_token_and_root_folder(monkeypatch):
    async def no_token(*_args):
        return None

    monkeypatch.setattr("app.services.storage_discovery.get_fresh_token", no_token)
    with pytest.raises(ValueError, match="credentials"):
        await StorageDiscovery()._google(None, "tenant", {"id": "root"})

    async def token(*_args):
        return "token"

    monkeypatch.setattr("app.services.storage_discovery.get_fresh_token", token)

    async def handler(request):
        return httpx.Response(200, json={"id": "root", "mimeType": "text/plain"})

    _client(monkeypatch, handler)
    with pytest.raises(ValueError, match="not a folder"):
        await StorageDiscovery()._google(None, "tenant", {"id": "root"})


@pytest.mark.asyncio
async def test_google_incomplete_search_and_escaped_parent_are_rejected(monkeypatch):
    async def token(*_args):
        return "token"

    monkeypatch.setattr("app.services.storage_discovery.get_fresh_token", token)
    seen = []

    async def handler(request):
        seen.append(request)
        if request.url.path.endswith("/files/root"):
            return httpx.Response(200, json={"id": "root", "name": "Root", "mimeType": "application/vnd.google-apps.folder"})
        return httpx.Response(200, json={"incompleteSearch": True, "files": []})

    _client(monkeypatch, handler)
    with pytest.raises(ValueError, match="incomplete"):
        await StorageDiscovery()._google(None, "tenant", {"id": "root"})
    assert seen[-1].url.params["q"].startswith("'root' in parents")


@pytest.mark.asyncio
async def test_graph_requires_sharepoint_drive_and_rejects_hostile_continuation(monkeypatch):
    async def token(*_args):
        return "token"

    monkeypatch.setattr("app.services.storage_discovery.get_fresh_token", token)
    with pytest.raises(ValueError, match="drive_id"):
        await StorageDiscovery()._graph(None, "tenant", "sharepoint", {"id": "root"})

    async def handler(request):
        if request.url.path.endswith("/items/root"):
            return httpx.Response(200, json={"id": "root", "name": "Root", "folder": {}})
        return httpx.Response(200, json={"value": [], "@odata.nextLink": "https://evil.example/items/root/children"})

    _client(monkeypatch, handler)
    with pytest.raises(ValueError, match="trusted"):
        await StorageDiscovery()._graph(None, "tenant", "sharepoint", {"id": "root", "drive_id": "drive"})


@pytest.mark.asyncio
async def test_graph_preserves_metadata_and_streams_marker_limit(monkeypatch):
    async def token(*_args):
        return "token"

    monkeypatch.setattr("app.services.storage_discovery.get_fresh_token", token)

    async def handler(request):
        if request.url.path.endswith("/items/root"):
            return httpx.Response(200, json={"id": "root", "name": "Root", "folder": {}})
        if request.url.path.endswith("/children"):
            return httpx.Response(200, json={"value": [{
                "id": "file", "name": "doc.txt", "size": 7, "eTag": "etag",
                "file": {"mimeType": "text/plain", "hashes": {"sha256Hash": "hash"}},
                "fileSystemInfo": {"lastModifiedDateTime": "2026-01-02T03:04:05Z"},
                "webUrl": "https://example/doc", "parentReference": {"id": "root", "driveId": "drive"},
            }]})
        return httpx.Response(404)

    _client(monkeypatch, handler)
    items = await StorageDiscovery()._graph(None, "tenant", "sharepoint", {"id": "root", "drive_id": "drive"})
    item = items[0]
    assert item["sha256"] == "hash"
    assert item["etag"] == "etag"
    assert item["mime_type"] == "text/plain"
    assert item["modified_time"].year == 2026


@pytest.mark.asyncio
async def test_google_oversized_marker_is_rejected(monkeypatch):
    async def token(*_args):
        return "token"

    monkeypatch.setattr("app.services.storage_discovery.get_fresh_token", token)

    async def handler(request):
        if request.url.path.endswith("/files/root"):
            return httpx.Response(200, json={"id": "root", "mimeType": "application/vnd.google-apps.folder"})
        if request.url.path.endswith("/files/marker"):
            return httpx.Response(200, content=b"{" + b"a" * 4096 + b"}")
        return httpx.Response(200, json={"files": [{"id": "marker", "name": ".lawhand-matter.json", "parents": ["root"]}]})

    _client(monkeypatch, handler)
    with pytest.raises(ValueError, match="marker"):
        await StorageDiscovery()._google(None, "tenant", {"id": "root"})


@pytest.mark.asyncio
async def test_google_duplicate_markers_for_one_folder_are_rejected(monkeypatch):
    async def token(*_args):
        return "token"

    monkeypatch.setattr("app.services.storage_discovery.get_fresh_token", token)

    async def handler(request):
        if request.url.path.endswith("/files/root"):
            return httpx.Response(200, json={"id": "root", "mimeType": "application/vnd.google-apps.folder"})
        if request.url.path.endswith("/files/one") or request.url.path.endswith("/files/two"):
            return httpx.Response(200, text="{}")
        return httpx.Response(200, json={"files": [
            {"id": "one", "name": ".lawhand-matter.json", "parents": ["root"]},
            {"id": "two", "name": ".lawhand-matter.json", "parents": ["root"]},
        ]})

    _client(monkeypatch, handler)
    with pytest.raises(ValueError, match="Duplicate matter markers"):
        await StorageDiscovery()._google(None, "tenant", {"id": "root"})
