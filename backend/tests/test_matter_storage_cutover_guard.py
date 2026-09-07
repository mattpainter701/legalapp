from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest

from app.services import matter_file_store as module
from app.services.matter_file_store import MatterFileStoragePolicyError, MatterFileStore


def scalar(value):
    return Mock(scalar_one_or_none=Mock(return_value=value))


@pytest.mark.asyncio
@pytest.mark.parametrize('provider', ['onedrive', 'google_drive', 'sharepoint'])
async def test_upload_lock_rejects_retired_provider(monkeypatch, provider):
    monkeypatch.setattr('app.database.set_tenant_context', AsyncMock())
    db = Mock(execute=AsyncMock(side_effect=[scalar(None), scalar('google_drive' if provider != 'google_drive' else 'onedrive')]))
    with pytest.raises(MatterFileStoragePolicyError, match='storage provider changed'):
        await MatterFileStore()._lock_write_binding(db, str(uuid4()), 'case', provider, 'old-folder')
    assert 'FOR UPDATE' in str(db.execute.await_args_list[0].args[0])


@pytest.mark.asyncio
async def test_upload_lock_rejects_stale_same_provider_binding(monkeypatch):
    monkeypatch.setattr('app.database.set_tenant_context', AsyncMock())
    db = Mock(execute=AsyncMock(side_effect=[scalar(None), scalar('onedrive'), scalar({'onedrive': {'matter_folder_id': 'new-folder'}})]))
    with pytest.raises(MatterFileStoragePolicyError, match='folder binding changed'):
        await MatterFileStore()._lock_write_binding(db, str(uuid4()), 'case', 'onedrive', 'old-folder')


@pytest.mark.asyncio
async def test_upload_lock_accepts_current_binding(monkeypatch):
    context = AsyncMock()
    monkeypatch.setattr('app.database.set_tenant_context', context)
    tenant = str(uuid4())
    db = Mock(execute=AsyncMock(side_effect=[scalar(None), scalar('google_drive'), scalar({'google_drive': {'matter_folder_id': 'folder'}})]))
    await MatterFileStore()._lock_write_binding(db, tenant, 'case', 'google_drive', 'folder')
    context.assert_awaited_once_with(db, tenant)


@pytest.mark.asyncio
async def test_sharepoint_migrated_binding_creates_category_below_matter(monkeypatch):
    store = MatterFileStore()
    monkeypatch.setattr(module, 'get_fresh_token', AsyncMock(return_value='token'))
    ensure = AsyncMock(return_value='category')
    monkeypatch.setattr(module, '_ensure_sharepoint_path', ensure)
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(201, json={'id': 'file', 'webUrl': 'https://example.invalid/file'})
    client_type = httpx.AsyncClient
    monkeypatch.setattr(module.httpx, 'AsyncClient', lambda **kwargs: client_type(transport=httpx.MockTransport(handler), **kwargs))
    result = await store._try_store_sharepoint(None, str(uuid4()), 'file.pdf', b'bytes', 'application/pdf', folder_id=None, drive_id='drive', matter_folder_id='matter-root', folder_path=['client_uploads', 'Imported folder'])
    assert result.succeeded
    ensure.assert_awaited_once_with('token', 'drive', ['client_uploads', 'Imported folder'], 'matter-root')
    assert '/items/category:/' in requests[0].url.path
