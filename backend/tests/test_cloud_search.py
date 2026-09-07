"""Google Drive search request options, including Shared Drive support."""

import pytest

from app.services.cloud_search import CloudSearchService


class _Response:
    status_code = 200
    text = ""

    def json(self):
        return {"files": []}


@pytest.mark.asyncio
@pytest.mark.parametrize("account_tier", ["personal", "workspace"])
async def test_drive_search_includes_shared_drive_options(monkeypatch, account_tier):
    """The same request is safe for personal accounts and Workspace tenants."""
    service = CloudSearchService()
    captured: dict = {}

    async def fake_token(*_args, **_kwargs):
        return "access-token"

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _url, *, headers, params):
            captured.update(params)
            return _Response()

    monkeypatch.setattr(service, "_get_google_token", fake_token)
    monkeypatch.setattr("app.services.cloud_search.httpx.AsyncClient", lambda **_kwargs: _Client())

    await service._search_google_drive(
        db=None,
        keywords=["matter"],
        date_after="",
        max_hits=10,
        tenant_id=f"tenant-{account_tier}",
        user_id=None,
    )

    assert captured["supportsAllDrives"] is True
    assert captured["includeItemsFromAllDrives"] is True
    assert captured["corpora"] == "allDrives"
