import json

import httpx
import pytest

from app.services import matter_folder_marker as marker_module
from app.services.matter_folder_marker import MatterMarkerConflict, ensure_marker, validate_marker

IDENTITY = {'schema_version': 1, 'tenant_id': 'tenant-a', 'matter_id': 'matter-a'}


@pytest.mark.parametrize('content', [b'[]', b'invalid', b'x' * 4097, b'{"schema_version":2}', json.dumps({**IDENTITY, 'tenant_id': 'tenant-b'}).encode(), json.dumps({**IDENTITY, 'matter_id': 'matter-b'}).encode()])
def test_foreign_or_invalid_marker_conflicts(content):
    with pytest.raises(MatterMarkerConflict):
        validate_marker(content, IDENTITY)


@pytest.mark.asyncio
@pytest.mark.parametrize('provider', ['google_drive', 'onedrive', 'sharepoint'])
async def test_existing_marker_is_read_and_never_overwritten(monkeypatch, provider):
    calls = []
    def handler(request):
        calls.append(request)
        if request.url.host == 'download.test' or request.url.params.get('alt') == 'media':
            if request.url.host == 'download.test':
                assert 'authorization' not in request.headers
            return httpx.Response(200, json=IDENTITY)
        if provider == 'google_drive':
            return httpx.Response(200, json={'files': [{'id': 'marker-id'}]})
        return httpx.Response(200, json={'@microsoft.graph.downloadUrl': 'https://download.test/marker'})
    cls = httpx.AsyncClient
    monkeypatch.setattr(marker_module.httpx, 'AsyncClient', lambda **kwargs: cls(transport=httpx.MockTransport(handler), **kwargs))
    await ensure_marker('secret', provider, {'matter_folder_id': 'folder', 'drive_id': 'drive'}, IDENTITY)
    assert len(calls) == 2
    assert all(request.method == 'GET' for request in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize('provider', ['google_drive', 'onedrive', 'sharepoint'])
async def test_missing_marker_is_written_with_identity(monkeypatch, provider):
    writes = []
    def handler(request):
        if request.method == 'GET':
            return httpx.Response(200, json={'files': []}) if provider == 'google_drive' else httpx.Response(404)
        writes.append(request)
        assert b'tenant-a' in request.content and b'matter-a' in request.content
        return httpx.Response(201, json={'id': 'new-marker'})
    cls = httpx.AsyncClient
    monkeypatch.setattr(marker_module.httpx, 'AsyncClient', lambda **kwargs: cls(transport=httpx.MockTransport(handler), **kwargs))
    await ensure_marker('secret', provider, {'matter_folder_id': 'folder', 'drive_id': 'drive'}, IDENTITY)
    assert len(writes) == 1


@pytest.mark.asyncio
async def test_duplicate_google_markers_are_a_hard_conflict(monkeypatch):
    cls = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={'files': [{'id': 'a'}, {'id': 'b'}]}))
    monkeypatch.setattr(marker_module.httpx, 'AsyncClient', lambda **kwargs: cls(transport=transport, **kwargs))
    with pytest.raises(MatterMarkerConflict, match='Multiple'):
        await ensure_marker('secret', 'google_drive', {'matter_folder_id': 'folder'}, IDENTITY)
