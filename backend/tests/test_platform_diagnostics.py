"""Operator troubleshooting routes: what they expose, and to whom."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.models.api_access_log import ApiAccessLog
from app.models.error_log import ErrorLog
from app.models.integration_sync_run import IntegrationSyncRun
from app.models.tenant import Tenant
from app.models.user import User
from tests.platform_auth_helpers import platform_headers


DEBUG_HEADERS = lambda: platform_headers(["platform:debug"])  # noqa: E731
READ_ONLY_HEADERS = lambda: platform_headers(["platform:read"])  # noqa: E731

TRACE_ID = "req-abc-123"


@pytest_asyncio.fixture
async def other_tenant(db_session):
    """A second tenant, so isolation can be asserted rather than assumed."""

    tenant = Tenant(
        id=uuid.uuid4(),
        name="Rival Legal LLP",
        domain="rivallegal.com",
        billing_tier="payg",
        is_active=True,
    )
    db_session.add(tenant)
    await db_session.commit()
    await db_session.refresh(tenant)
    return tenant


@pytest_asyncio.fixture
async def seeded_error(db_session, test_tenant, test_user):
    row = ErrorLog(
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        error_type="llm_error",
        severity="error",
        message="Upstream gateway returned 502",
        stack_trace="Traceback (most recent call last):\n  ...\nRuntimeError: boom",
        endpoint="/api/chat",
        method="POST",
        status_code=500,
        ip_address="203.0.113.7",
        user_agent="Mozilla/5.0",
        request_id=TRACE_ID,
        query_text="what is the filing deadline",
    )
    db_session.add(row)
    db_session.add(
        ApiAccessLog(
            tenant_id=test_tenant.id,
            user_id=test_user.id,
            endpoint="/api/chat",
            method="POST",
            status_code=500,
            latency_ms=1842.5,
            ip_address="203.0.113.7",
            request_id=TRACE_ID,
        )
    )
    await db_session.commit()
    await db_session.refresh(row)
    return row


# ── The gate ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_platform_read_cannot_reach_troubleshooting_data(
    client: AsyncClient, seeded_error, test_tenant
):
    """platform:read must never imply platform:debug."""

    for method, path in (
        ("get", f"/api/platform/logs/{seeded_error.id}"),
        ("get", f"/api/platform/trace/{TRACE_ID}"),
        ("get", f"/api/platform/tenants/{test_tenant.id}/diagnostics"),
        ("get", "/api/platform/audit"),
        ("get", "/api/platform/users?email=attorney"),
    ):
        response = await getattr(client, method)(path, headers=READ_ONLY_HEADERS())
        assert response.status_code == 403, f"{method.upper()} {path}"

    patched = await client.patch(
        f"/api/platform/logs/{seeded_error.id}/resolve",
        headers=READ_ONLY_HEADERS(),
        json={"is_resolved": True},
    )
    assert patched.status_code == 403


@pytest.mark.asyncio
async def test_troubleshooting_routes_reject_an_unauthenticated_caller(
    client: AsyncClient, seeded_error
):
    response = await client.get(
        f"/api/platform/logs/{seeded_error.id}", headers={"Authorization": "Bearer "}
    )
    assert response.status_code == 403


# ── Error detail ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_error_id_from_a_customer_resolves_to_the_full_record(
    client: AsyncClient, seeded_error, test_tenant
):
    response = await client.get(
        f"/api/platform/logs/{seeded_error.id}", headers=DEBUG_HEADERS()
    )
    assert response.status_code == 200
    body = response.json()

    # The fields that were written but previously unreadable over the API.
    assert "RuntimeError: boom" in body["stack_trace"]
    assert body["request_id"] == TRACE_ID
    assert body["query_text"] == "what is the filing deadline"
    assert body["ip_address"] == "203.0.113.7"
    assert body["user_agent"] == "Mozilla/5.0"

    assert body["tenant_id"] == str(test_tenant.id)
    assert body["tenant_name"] == test_tenant.name
    assert body["endpoint"] == "/api/chat"
    assert body["status_code"] == 500


@pytest.mark.asyncio
async def test_error_lookup_is_scoped_when_a_tenant_hint_is_given(
    client: AsyncClient, seeded_error, other_tenant
):
    """The hint narrows the search; it must never widen it.

    Asking for tenant B's view of tenant A's error returns nothing, so the
    parameter cannot be used to confirm a row exists outside the named tenant.
    """

    response = await client.get(
        f"/api/platform/logs/{seeded_error.id}?tenant_id={other_tenant.id}",
        headers=DEBUG_HEADERS(),
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_missing_and_malformed_error_ids(client: AsyncClient):
    absent = await client.get(
        f"/api/platform/logs/{uuid.uuid4()}", headers=DEBUG_HEADERS()
    )
    assert absent.status_code == 404

    malformed = await client.get(
        "/api/platform/logs/not-a-uuid", headers=DEBUG_HEADERS()
    )
    assert malformed.status_code == 400


@pytest.mark.asyncio
async def test_error_list_route_still_answers_to_platform_read(
    client: AsyncClient, seeded_error
):
    """Adding the detail route must not have moved the existing list route."""

    response = await client.get("/api/platform/logs", headers=READ_ONLY_HEADERS())
    assert response.status_code == 200

    summary = await client.get(
        "/api/platform/logs/summary", headers=READ_ONLY_HEADERS()
    )
    assert summary.status_code == 200
    # /logs/summary must not have been captured by /logs/{error_id}.
    assert "total_errors" in summary.json()


@pytest.mark.asyncio
async def test_list_view_still_abbreviates_the_user_id(
    client: AsyncClient, seeded_error
):
    response = await client.get("/api/platform/logs", headers=READ_ONLY_HEADERS())
    entry = next(
        e for e in response.json()["errors"] if e["id"] == str(seeded_error.id)
    )
    assert entry["user_id"].endswith("…")
    assert "stack_trace" not in entry


# ── Resolution ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_operator_can_close_out_an_error_they_diagnosed(
    client: AsyncClient, seeded_error
):
    response = await client.patch(
        f"/api/platform/logs/{seeded_error.id}/resolve",
        headers=DEBUG_HEADERS(),
        json={"is_resolved": True, "resolution_notes": "Upstream gateway restarted"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["is_resolved"] is True
    assert body["resolved_at"] is not None
    assert body["resolution_notes"] == "Upstream gateway restarted"

    reread = await client.get(
        f"/api/platform/logs/{seeded_error.id}", headers=DEBUG_HEADERS()
    )
    assert reread.json()["is_resolved"] is True


@pytest.mark.asyncio
async def test_resolution_is_recorded_in_the_operator_audit_trail(
    client: AsyncClient, seeded_error
):
    await client.patch(
        f"/api/platform/logs/{seeded_error.id}/resolve",
        headers=DEBUG_HEADERS(),
        json={"is_resolved": True},
    )

    audit = await client.get(
        "/api/platform/audit?action=platform.error.resolved", headers=DEBUG_HEADERS()
    )
    assert audit.status_code == 200
    entries = audit.json()["entries"]
    assert any(e["resource_id"] == str(seeded_error.id) for e in entries)


# ── Trace correlation ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_request_id_correlates_the_error_with_the_request(
    client: AsyncClient, seeded_error, test_tenant
):
    response = await client.get(
        f"/api/platform/trace/{TRACE_ID}", headers=DEBUG_HEADERS()
    )
    assert response.status_code == 200
    body = response.json()

    assert body["request_id"] == TRACE_ID
    assert body["tenant_ids"] == [str(test_tenant.id)]
    assert [e["id"] for e in body["errors"]] == [str(seeded_error.id)]
    assert len(body["access_entries"]) == 1
    assert body["access_entries"][0]["endpoint"] == "/api/chat"
    assert body["access_entries"][0]["latency_ms"] == 1842.5


@pytest.mark.asyncio
async def test_trace_of_an_unknown_request_is_empty_not_an_error(
    client: AsyncClient, seeded_error
):
    response = await client.get(
        "/api/platform/trace/no-such-request", headers=DEBUG_HEADERS()
    )
    assert response.status_code == 200
    assert response.json()["errors"] == []
    assert response.json()["access_entries"] == []


# ── Tenant diagnostics ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tenant_diagnostics_summarises_what_is_failing(
    client: AsyncClient, db_session, seeded_error, test_tenant
):
    db_session.add(
        IntegrationSyncRun(
            tenant_id=test_tenant.id,
            provider="google",
            job_type="calendar_sync",
            status="failed",
            started_at=datetime.now(timezone.utc) - timedelta(minutes=5),
            items_ok=0,
            items_failed=12,
            error_summary="invalid_grant",
        )
    )
    await db_session.commit()

    response = await client.get(
        f"/api/platform/tenants/{test_tenant.id}/diagnostics", headers=DEBUG_HEADERS()
    )
    assert response.status_code == 200
    body = response.json()

    assert body["tenant_name"] == test_tenant.name
    assert body["is_active"] is True
    assert body["errors_by_severity"].get("error") == 1
    assert body["unresolved_errors"] == 1
    assert body["requests"] == 1
    # One access-log row, status 500 → the whole window failed.
    assert body["error_rate"] == 1.0
    assert body["top_failing_endpoints"][0]["endpoint"] == "/api/chat"

    assert len(body["failed_sync_runs"]) == 1
    assert body["failed_sync_runs"][0]["provider"] == "google"
    assert body["failed_sync_runs"][0]["error_summary"] == "invalid_grant"
    assert body["last_activity_at"] is not None


@pytest.mark.asyncio
async def test_tenant_diagnostics_does_not_count_another_tenants_failures(
    client: AsyncClient, seeded_error, other_tenant
):
    """Tenant B's diagnostics must not surface tenant A's errors."""

    response = await client.get(
        f"/api/platform/tenants/{other_tenant.id}/diagnostics", headers=DEBUG_HEADERS()
    )
    assert response.status_code == 200
    body = response.json()

    assert body["tenant_id"] == str(other_tenant.id)
    assert body["requests"] == 0
    assert body["unresolved_errors"] == 0
    assert body["errors_by_severity"] == {}
    assert body["failed_sync_runs"] == []
    assert body["top_failing_endpoints"] == []


@pytest.mark.asyncio
async def test_diagnostics_for_an_unknown_tenant(client: AsyncClient):
    response = await client.get(
        f"/api/platform/tenants/{uuid.uuid4()}/diagnostics", headers=DEBUG_HEADERS()
    )
    assert response.status_code == 404


# ── Operator audit ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_operator_audit_trail_is_readable(client: AsyncClient, test_tenant):
    # Any platform call writes an audit row via the audit middleware.
    await client.get("/api/platform/plans", headers=platform_headers())

    response = await client.get("/api/platform/audit", headers=DEBUG_HEADERS())
    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 1
    assert any(e["action"] == "platform.request" for e in body["entries"])
    assert all("created_at" in e for e in body["entries"])


@pytest.mark.asyncio
async def test_operator_audit_filters_by_action(client: AsyncClient):
    await client.get("/api/platform/plans", headers=platform_headers())

    response = await client.get(
        "/api/platform/audit?action=platform.request", headers=DEBUG_HEADERS()
    )
    assert response.status_code == 200
    assert all(e["action"] == "platform.request" for e in response.json()["entries"])


# ── User lookup ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_support_can_find_a_users_tenant_from_their_email(
    client: AsyncClient, test_user, test_tenant
):
    response = await client.get(
        "/api/platform/users?email=attorney@testfirm.com", headers=DEBUG_HEADERS()
    )
    assert response.status_code == 200
    results = response.json()
    assert len(results) == 1
    assert results[0]["id"] == str(test_user.id)
    assert results[0]["tenant_id"] == str(test_tenant.id)
    assert results[0]["tenant_name"] == test_tenant.name


@pytest.mark.asyncio
async def test_user_lookup_matches_on_a_partial_address(client: AsyncClient, test_user):
    response = await client.get(
        "/api/platform/users?email=TESTFIRM.COM", headers=DEBUG_HEADERS()
    )
    assert response.status_code == 200
    assert [r["id"] for r in response.json()] == [str(test_user.id)]


@pytest.mark.asyncio
async def test_user_lookup_returns_nothing_for_an_unknown_address(
    client: AsyncClient, test_user
):
    response = await client.get(
        "/api/platform/users?email=nobody@example.org", headers=DEBUG_HEADERS()
    )
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_user_lookup_spans_tenants(
    client: AsyncClient, db_session, test_user, other_tenant
):
    """Support starts from an email without knowing the tenant."""

    db_session.add(
        User(
            id=uuid.uuid4(),
            tenant_id=other_tenant.id,
            email="attorney@rivallegal.com",
            full_name="Rival Attorney",
            role="admin",
            oauth_provider="google",
            oauth_subject="google-sub-999",
            is_active=True,
        )
    )
    await db_session.commit()

    response = await client.get(
        "/api/platform/users?email=attorney@", headers=DEBUG_HEADERS()
    )
    assert response.status_code == 200
    tenants = {r["tenant_id"] for r in response.json()}
    assert tenants == {str(test_user.tenant_id), str(other_tenant.id)}
