from unittest.mock import AsyncMock, Mock, patch
from types import SimpleNamespace
import uuid

import pytest

from app.services import account_detect, capabilities
from app.services.user_sync import UserSyncService
from app.routers import admin


def test_google_claim_detection():
    assert account_detect.detect_google({"email": "a@firm.com", "hd": "firm.com"}) == ("workspace", "firm.com")
    assert account_detect.detect_google({"email": "a@gmail.com"}) == ("personal", None)
    assert account_detect.detect_google({"email": "a@firm.com", "hd": "bad domain"}) == ("unknown", None)


def test_microsoft_claim_detection():
    assert account_detect.detect_microsoft({"tid": account_detect.CONSUMER_TID}) == ("consumer", None)
    assert account_detect.detect_microsoft({"tid": "00000000-0000-0000-0000-000000000001", "preferred_username": "a@firm.com"}) == ("azure_ad", "firm.com")
    assert account_detect.detect_microsoft({"tid": "not-a-tenant"}) == ("unknown", None)


def test_personal_google_directory_is_not_an_error():
    matrix = capabilities.resolve("google", "personal", "https://www.googleapis.com/auth/drive", None)
    assert matrix["directory_sync"] == {
        "available": False,
        "status": "unavailable",
        "reason": matrix["directory_sync"]["reason"],
    }
    assert "personal" in matrix["directory_sync"]["reason"].lower()
    assert matrix["cloud_storage"]["available"] is True


def test_workspace_directory_scope_and_failure_states():
    scope = capabilities.GOOGLE_DIRECTORY_SCOPE
    assert capabilities.resolve("google", "workspace", scope, "ok")["directory_sync"]["status"] == "ok"
    assert capabilities.resolve("google", "workspace", "", None)["directory_sync"]["status"] == "needs_reauth"
    assert capabilities.resolve("google", "workspace", scope, "failed")["directory_sync"]["status"] == "error"


def test_microsoft_teams_matrix():
    consumer = capabilities.resolve("microsoft", "consumer", "User.Read", None)
    assert consumer["directory_sync"]["available"] is False
    assert consumer["teams"]["available"] is False
    assert "teams" not in capabilities.resolve("google", "workspace", capabilities.GOOGLE_DIRECTORY_SCOPE, "ok")


@pytest.mark.asyncio
async def test_google_backfill_success_and_failure():
    response = Mock(status_code=200)
    response.json.return_value = {"email": "admin@firm.com", "hd": "firm.com"}
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.__aexit__.return_value = False
    client.get.return_value = response
    with patch("app.services.account_detect.httpx.AsyncClient", return_value=client):
        assert await account_detect.backfill_google("token") == ("workspace", "firm.com")
    response.status_code = 401
    with patch("app.services.account_detect.httpx.AsyncClient", return_value=client):
        assert await account_detect.backfill_google("token") == ("unknown", None)


@pytest.mark.asyncio
async def test_unknown_credential_backfill_commits_attempt_and_reloads_rows():
    tenant_id = uuid.uuid4()
    credential = SimpleNamespace(
        tenant_id=tenant_id, provider="google", account_detected_at=None,
        account_type=None, account_domain=None,
    )
    refreshed = SimpleNamespace(
        tenant_id=tenant_id, provider="google", account_detected_at=credential.account_detected_at,
        account_type="workspace", account_domain="firm.example",
    )
    db = AsyncMock()
    db.execute.side_effect = [
        SimpleNamespace(scalar_one_or_none=lambda: credential),
        SimpleNamespace(scalar_one_or_none=lambda: refreshed),
        SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [refreshed])),
    ]
    with patch("app.services.account_detect.set_tenant_context", AsyncMock()), \
         patch("app.services.account_detect.get_fresh_token", AsyncMock(return_value="token")), \
         patch("app.services.account_detect.backfill_google", AsyncMock(return_value=("workspace", "firm.example"))):
        result = await account_detect.backfill_unknown_credentials(db, str(tenant_id), [credential])
    assert result == [refreshed]
    assert credential.account_detected_at is not None
    assert db.commit.await_count == 2


@pytest.mark.asyncio
async def test_unknown_backfill_does_not_retry_credential_with_detection_attempt():
    tenant_id = uuid.uuid4()
    credential = SimpleNamespace(
        tenant_id=tenant_id, provider="microsoft", account_detected_at=object(),
        account_type=None, account_domain=None,
    )
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: credential)
    # The final fresh-row query is intentionally also represented by the same row.
    db.execute.side_effect = [
        SimpleNamespace(scalar_one_or_none=lambda: credential),
        SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [credential])),
    ]
    with patch("app.services.account_detect.set_tenant_context", AsyncMock()), \
         patch("app.services.account_detect.get_fresh_token", AsyncMock()) as token:
        result = await account_detect.backfill_unknown_credentials(db, str(tenant_id), [credential])
    assert result == [credential]
    token.assert_not_awaited()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_backfill_rolls_back_and_restores_tenant_context_on_failure():
    tenant_id = uuid.uuid4()
    credential = SimpleNamespace(
        tenant_id=tenant_id, provider="google", account_detected_at=None,
        account_type=None, account_domain=None,
    )
    db = AsyncMock()
    db.execute.side_effect = [
        SimpleNamespace(scalar_one_or_none=lambda: credential),
        SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [credential])),
    ]
    context = AsyncMock()
    with patch("app.services.account_detect.set_tenant_context", context), \
         patch("app.services.account_detect.get_fresh_token", AsyncMock(side_effect=RuntimeError("refresh failed"))):
        result = await account_detect.backfill_unknown_credentials(
            db, str(tenant_id), [credential]
        )
    assert result == [credential]
    db.rollback.assert_awaited_once()
    assert context.await_count >= 4


@pytest.mark.asyncio
async def test_personal_google_sync_is_not_applicable_and_persists_state():
    credential = SimpleNamespace(
        account_type="personal", scopes="openid https://www.googleapis.com/auth/drive",
        last_user_sync_status=None,
    )
    update_result = SimpleNamespace(rowcount=1)
    db = AsyncMock()
    db.execute.side_effect = [SimpleNamespace(scalar_one_or_none=lambda: credential), update_result]
    result = await UserSyncService()._directory_sync_blocked(
        db, str(uuid.uuid4()), "google"
    )
    assert result["status"] == "not_applicable"
    assert "personal" in result["reason"].lower()
    assert db.commit.await_count == 1


@pytest.mark.asyncio
async def test_integration_health_consumes_refreshed_detection_rows():
    tenant_id = uuid.uuid4()
    credential = SimpleNamespace(
        provider="google", tenant_id=tenant_id, account_type=None,
        account_domain=None, account_detected_at=None,
        granted_by_user_id=None, service_account_email="admin@firm.example",
        health="healthy", token_expires_at=None, created_at=None,
        last_refresh_at=None, last_refresh_error=None,
    )
    refreshed_data = vars(credential).copy()
    refreshed_data.update(account_type="workspace", account_domain="firm.example")
    refreshed = SimpleNamespace(**refreshed_data)
    db = AsyncMock()
    db.execute.side_effect = [
        SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [credential])),
        SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [])),
    ]
    request = SimpleNamespace(state=SimpleNamespace(tenant_id=str(tenant_id)))
    with patch("app.routers.admin._require_admin", AsyncMock()), \
         patch("app.routers.admin.set_tenant_context", AsyncMock()), \
         patch("app.routers.admin.account_detect.backfill_unknown_credentials", AsyncMock(return_value=[refreshed])) as backfill:
        result = await admin.integration_health(request, db)
    backfill.assert_awaited_once_with(db, str(tenant_id), [credential])
    assert result["providers"]["google"]["connected"] is True
    assert result["providers"]["google"]["service_account_email"] == "admin@firm.example"
    assert result["overall_health"] == "healthy"


@pytest.mark.asyncio
async def test_microsoft_backfill_success_and_malformed_response():
    response = Mock(status_code=200)
    response.json.return_value = {"value": [{"verifiedDomains": [{"name": "firm.com", "isDefault": True}]}]}
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.__aexit__.return_value = False
    client.get.return_value = response
    with patch("app.services.account_detect.httpx.AsyncClient", return_value=client):
        assert await account_detect.backfill_microsoft("token") == ("azure_ad", "firm.com")
    response.json.return_value = {"value": []}
    with patch("app.services.account_detect.httpx.AsyncClient", return_value=client):
        assert await account_detect.backfill_microsoft("token") == ("unknown", None)


def test_all_provider_tiers_have_conservative_directory_matrix():
    cases = [
        ("google", "workspace", True),
        ("google", "personal", False),
        ("microsoft", "azure_ad", True),
        ("microsoft", "consumer", False),
        ("google", None, False),
        ("microsoft", "unknown", False),
    ]
    for provider, tier, expected in cases:
        scope = capabilities.GOOGLE_DIRECTORY_SCOPE if provider == "google" else capabilities.MS_DIRECTORY_SCOPE
        assert capabilities.resolve(provider, tier, scope, None)["directory_sync"]["available"] is expected


def test_personal_and_consumer_required_scopes_exclude_directory_consent():
    google = ["openid", capabilities.GOOGLE_DIRECTORY_SCOPE, "https://www.googleapis.com/auth/drive"]
    microsoft = ["User.Read", capabilities.MS_DIRECTORY_SCOPE]
    assert capabilities.effective_required_scopes("google", google, "personal") == ["openid", "https://www.googleapis.com/auth/drive"]
    assert capabilities.effective_required_scopes("microsoft", microsoft, "consumer") == ["User.Read"]
    assert capabilities.effective_required_scopes("google", google, "workspace") == google
