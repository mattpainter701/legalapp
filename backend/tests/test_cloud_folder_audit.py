"""Read-only cloud folder audit coverage."""

from types import SimpleNamespace

import pytest

from app.services import cloud_folder_audit as audit


class _Rows:
    def __init__(self, values): self.values = values
    def scalars(self): return self
    def all(self): return self.values


class _Db:
    def __init__(self, tenant, matters): self.tenant, self.matters = tenant, matters
    async def scalar(self, _statement): return self.tenant
    async def execute(self, _statement): return _Rows(self.matters)


def _matter(matter_id="12345678-aaaa-bbbb-cccc-123456789abc"):
    return SimpleNamespace(
        id=matter_id, matter_name="Acme v Beta", slug="acme-v-beta",
        cloud_folder={"onedrive": {"matter_folder_id": "bound"}},
    )


TENANT_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


@pytest.mark.asyncio
async def test_audit_reports_provider_success_and_duplicates(monkeypatch):
    tenant = SimpleNamespace(
        id=TENANT_ID, cloud_root_folder={"onedrive": {"id": "od-root"}},
    )
    matter = _matter()
    children = [
        {"id": "bound", "name": "Acme v Beta (12345678)"},
        {"id": "duplicate", "name": "Acme v Beta (12345678)"},
        {"id": "slug", "name": "acme-v-beta"},
    ]
    async def token(*_args): return "token"
    async def listing(*_args): return children
    monkeypatch.setattr(audit, "get_fresh_token", token)
    monkeypatch.setattr(audit, "_list_children", listing)

    result = await audit.audit_matter_cloud_folders(_Db(tenant, [matter]), TENANT_ID)

    assert result["status"] == "complete"
    assert result["mutations_performed"] is False
    assert result["providers"]["onedrive"]["status"] == "ok"
    row = result["providers"]["onedrive"]["matters"][0]
    assert row["ambiguous"] is True
    assert "duplicate_matching_names" in row["ambiguity_reasons"]


@pytest.mark.asyncio
async def test_audit_marks_provider_failure_incomplete(monkeypatch):
    tenant = SimpleNamespace(id=TENANT_ID, cloud_root_folder={"google_drive": {"id": "gd-root"}})
    async def token(*_args): return "token"
    async def listing(*_args): raise RuntimeError("HTTP 503")
    monkeypatch.setattr(audit, "get_fresh_token", token)
    monkeypatch.setattr(audit, "_list_children", listing)

    result = await audit.audit_matter_cloud_folders(_Db(tenant, [_matter()]), TENANT_ID)

    assert result["status"] == "incomplete"
    assert result["complete"] is False
    assert result["providers"]["google_drive"]["status"] == "error"
    assert "HTTP 503" in result["providers"]["google_drive"]["error"]


@pytest.mark.asyncio
async def test_audit_reports_missing_token_as_provider_error(monkeypatch):
    tenant = SimpleNamespace(id=TENANT_ID, cloud_root_folder={"sharepoint": {"id": "sp-root", "drive_id": "drive"}})
    async def no_token(*_args): return None
    monkeypatch.setattr(audit, "get_fresh_token", no_token)

    result = await audit.audit_matter_cloud_folders(_Db(tenant, [_matter()]), TENANT_ID)

    assert result["status"] == "incomplete"
    assert result["providers"]["sharepoint"]["status"] == "error"
    assert "token unavailable" in result["providers"]["sharepoint"]["error"]


def test_classify_binding_not_found_is_ambiguous():
    result = audit.classify_matter_folders(
        matter_id="12345678-aaaa-bbbb-cccc-123456789abc",
        matter_name="Acme v Beta", matter_slug="acme-v-beta", current_binding_id="missing",
        children=[{"id": "other", "name": "Acme v Beta (12345678)"}],
    )
    assert result["ambiguous"] is True
    assert "binding_not_found_among_matching_folders" in result["ambiguity_reasons"]

@pytest.mark.asyncio
@pytest.mark.parametrize('provider', ['google_drive', 'onedrive', 'sharepoint'])
async def test_audit_walks_provider_pages_without_mutation(monkeypatch, provider):
    import httpx
    calls = []
    def handler(request):
        calls.append(request)
        assert request.method == 'GET'
        page2 = request.url.params.get('pageToken') == 'next' or request.url.params.get('$skiptoken') == 'next'
        if provider == 'google_drive':
            assert request.url.params['supportsAllDrives'] == 'true'
            assert request.url.params['includeItemsFromAllDrives'] == 'true'
            return httpx.Response(200, json={'files': [{'id': 'two' if page2 else 'one', 'name': 'Folder'}], **({} if page2 else {'nextPageToken': 'next'})})
        return httpx.Response(200, json={'value': [{'id': 'two' if page2 else 'one', 'name': 'Folder', 'folder': {}}, {'id': 'ignored-file', 'file': {}}], **({} if page2 else {'@odata.nextLink': 'https://graph.microsoft.com/v1.0/next?$skiptoken=next'})})
    client = httpx.AsyncClient
    monkeypatch.setattr(audit.httpx, 'AsyncClient', lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs))
    items = await audit._list_children(provider, 'token', {'id': 'root', 'drive_id': 'drive'})
    assert [i['id'] for i in items] == ['one', 'two']
    assert len(calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('provider', ['google_drive', 'onedrive', 'sharepoint'])
async def test_audit_provider_http_failure_is_not_an_empty_success(monkeypatch, provider):
    import httpx
    client = httpx.AsyncClient
    monkeypatch.setattr(audit.httpx, 'AsyncClient', lambda **kwargs: client(transport=httpx.MockTransport(lambda request: httpx.Response(403)), **kwargs))
    with pytest.raises(RuntimeError, match='HTTP 403'):
        await audit._list_children(provider, 'token', {'id': 'root', 'drive_id': 'drive'})


@pytest.mark.asyncio
@pytest.mark.parametrize('provider', ['google_drive', 'sharepoint'])
async def test_audit_rejects_missing_roots(provider):
    with pytest.raises(RuntimeError, match='missing|incomplete'):
        await audit._list_children(provider, 'token', {})
