import uuid
from unittest.mock import AsyncMock, Mock

import pytest

from app.services import matter_file_store as module
from app.services.matter_file_store import MatterFileStore, MatterFileStoragePolicyError


@pytest.mark.asyncio
async def test_upload_waits_for_provisioned_binding(monkeypatch):
    ready = {'_status': 'provisioned', 'google_drive': {'matter_folder_id': 'one-matter'}}
    db = Mock(execute=AsyncMock(return_value=Mock(scalar_one_or_none=Mock(return_value=ready))))
    pause = AsyncMock()
    monkeypatch.setattr(module.asyncio, 'sleep', pause)
    result = await MatterFileStore()._ready_cloud_binding(db, str(uuid.uuid4()), 'case', {'_status': 'provisioning'})
    assert result == ready
    pause.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['failed', 'provisioning'])
async def test_failed_or_stalled_provision_does_not_start_an_upload(monkeypatch, status):
    pending = {'_status': status}
    db = Mock(execute=AsyncMock(return_value=Mock(scalar_one_or_none=Mock(return_value=pending))))
    monkeypatch.setattr(module.asyncio, 'sleep', AsyncMock())
    with pytest.raises(MatterFileStoragePolicyError, match='provisioning failed|still being prepared'):
        await MatterFileStore().store_matter_file_result(db, str(uuid.uuid4()), 'case', 'documents', 'a.pdf', b'file', 'application/pdf', matter_cloud_folder=pending)


@pytest.mark.asyncio
@pytest.mark.parametrize('provider', ['onedrive', 'google_drive'])
async def test_missing_binding_never_creates_slug_tree(monkeypatch, provider):
    monkeypatch.setattr(module, 'get_fresh_token', AsyncMock(return_value='token'))
    traversal = AsyncMock()
    monkeypatch.setattr(module, '_ensure_onedrive_path' if provider == 'onedrive' else '_ensure_gdrive_path', traversal)
    method = getattr(MatterFileStore(), '_try_store_' + provider)
    result = await method(None, str(uuid.uuid4()), 'slug', 'documents', 'file.pdf', b'x', 'application/pdf')
    assert not result.succeeded
    assert 'not bound' in result.error
    traversal.assert_not_awaited()
