"""Provider-independent folder identity and binding regressions."""
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import cloud_init

MATTER_ID = uuid.UUID('12345678-1234-4567-8123-123456789012')


def test_folder_name_normalizes_unsafe_characters_and_always_has_identity():
    assert cloud_init.canonical_matter_folder_name('  Smith / Jones: Case  ', MATTER_ID) == 'Smith - Jones- Case (12345678)'
    assert cloud_init.canonical_matter_folder_name('', MATTER_ID, 'case-slug') == 'case-slug (12345678)'
    assert len(cloud_init.canonical_matter_folder_name('x' * 300, MATTER_ID)) <= 200


def test_logical_path_survives_provider_remap_and_rename():
    from app.routers.matters import _apply_cloud_provider_metadata
    matter = SimpleNamespace(id=MATTER_ID, slug='smith', matter_name='Smith', cloud_folder=None)
    first = _apply_cloud_provider_metadata(matter, 'onedrive', {'folder_name': 'Renamed by firm', 'subfolders': {'documents': 'd1'}})
    path = first['path']
    result = _apply_cloud_provider_metadata(matter, 'google_drive', {'folder_name': 'Moved again', 'subfolders': {'documents': 'd2'}})
    assert path == 'claritylegal-records/Smith (12345678)'
    assert result['path'] == path
    assert result['onedrive']['path'] == 'claritylegal-records/Renamed by firm'
    assert result['google_drive']['path'] == 'claritylegal-records/Moved again'


@pytest.mark.asyncio
@pytest.mark.parametrize('provider', ['onedrive', 'google_drive', 'sharepoint'])
async def test_provision_uses_same_name_and_reuses_saved_id(monkeypatch, provider):
    monkeypatch.setattr(cloud_init, 'get_fresh_token', AsyncMock(return_value='token'))
    ensure = AsyncMock(side_effect=lambda *args: 'created-' + args[-2])
    kind = {'onedrive': 'onedrive', 'google_drive': 'gdrive', 'sharepoint': 'sharepoint'}[provider]
    monkeypatch.setattr(cloud_init, f'_ensure_{kind}_folder', ensure)
    monkeypatch.setattr(cloud_init, f'_get_{kind}_folder_metadata', AsyncMock(return_value={'name': 'Firm renamed folder'}))
    marker = AsyncMock()
    monkeypatch.setattr(cloud_init, 'ensure_matter_marker', marker)
    root = {provider: {'id': 'root-id', 'drive_id': 'drive-id'}}
    result = await cloud_init.initialize_matter_folders(None, str(uuid.uuid4()), 'smith', root, folder_name='Smith', matter_id=MATTER_ID)
    assert ensure.call_args_list[0].args[-2] == 'Smith (12345678)'
    assert result['path'] == 'claritylegal-records/Smith (12345678)'
    assert 'emails' not in result[provider]['subfolders']
    marker.assert_awaited_once()
    ensure.reset_mock()
    result = await cloud_init.initialize_matter_folders(None, str(uuid.uuid4()), 'smith', root, folder_name='Smith', matter_id=MATTER_ID, existing_folder={provider: {'matter_folder_id': 'saved-id', 'drive_id': 'drive-id'}, 'path': 'historic/path'})
    assert result[provider]['matter_folder_id'] == 'saved-id'
    assert result['path'] == 'historic/path'
    assert all(call.args[-1] == 'saved-id' for call in ensure.call_args_list)

@pytest.mark.asyncio
async def test_provision_reloads_binding_after_refresh_under_tenant_and_matter_locks(monkeypatch):
    from unittest.mock import Mock
    current_root = {'google_drive': {'id': 'current-root'}}
    matter = SimpleNamespace(cloud_folder={'path': 'historic/path', 'google_drive': {'matter_folder_id': 'saved', 'subfolders': {'documents': 'doc-id'}}})
    statements = []
    async def execute(statement):
        statements.append(statement)
        return Mock(scalar_one_or_none=Mock(return_value=current_root if len(statements) == 1 else matter))
    db = Mock(execute=AsyncMock(side_effect=execute), flush=AsyncMock())
    monkeypatch.setattr('app.database.set_tenant_context', AsyncMock())
    monkeypatch.setattr(cloud_init, 'get_fresh_token', AsyncMock(return_value='token'))
    ensure = AsyncMock(return_value='subfolder')
    monkeypatch.setattr(cloud_init, '_ensure_gdrive_folder', ensure)
    monkeypatch.setattr(cloud_init, '_get_gdrive_folder_metadata', AsyncMock(return_value={'name': 'Renamed', 'driveId': 'shared-drive'}))
    monkeypatch.setattr(cloud_init, 'ensure_matter_marker', AsyncMock())
    result = await cloud_init.initialize_matter_folders(db, str(uuid.uuid4()), 'case', {'google_drive': {'id': 'stale-root'}}, matter_id=MATTER_ID)
    assert result['google_drive']['matter_folder_id'] == 'saved'
    assert result['google_drive']['drive_id'] == 'shared-drive'
    assert result['google_drive']['subfolders']['documents'] == 'doc-id'
    assert result['path'] == 'historic/path'
    assert matter.cloud_folder['_status'] == 'provisioned'
    assert all('FOR UPDATE' in str(stmt) for stmt in statements)
    assert 'tenants' in str(statements[0]) and 'matters' in str(statements[1])
    assert all(call.args[-1] == 'saved' for call in ensure.await_args_list)
    db.flush.assert_awaited_once()
