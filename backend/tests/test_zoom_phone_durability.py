import asyncio
import hashlib
import hmac
import json
import os
import secrets
import subprocess
import time
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database import set_tenant_context
from app.main import app as application
from app.models.communication_log import CommunicationLog
from app.models.contact import Contact
from app.models.durable_job import DurableJob
from app.models.tenant import Tenant
from app.models.tenant_credential import TenantCredential
from app.models.tenant_oauth_app import TenantOAuthApp
from app.routers import integrations as integrations_router
from app.services.durable_job_worker import (
    enqueue_zoom_phone_reconciliation_jobs,
    process_job,
)
from app.services.durable_jobs import enqueue_job
from app.services.token_vault import decrypt_token, encrypt_token
from app.services.zoom_phone import (
    ZoomPhonePermanentError,
    ZoomPhoneImportResult,
    ZoomPhoneIntegrationError,
    ZoomPhoneReauthorizationRequired,
    fetch_zoom_phone_call_history,
    fetch_zoom_phone_call_history_detail,
    get_zoom_phone_token,
    import_zoom_phone_webhook_job,
    import_zoom_phone_records,
    zoom_phone_webhook_jobs,
)


async def _configure_zoom(
    db,
    tenant,
    user,
    *,
    account_id="zoom-account-1",
    webhook_secret="zoom-webhook-secret",
    expired=False,
):
    app = TenantOAuthApp(
        tenant_id=tenant.id,
        provider="zoom_phone",
        encrypted_client_id=encrypt_token("zoom-client"),
        encrypted_client_secret=encrypt_token("zoom-client-secret"),
        encrypted_webhook_secret_token=encrypt_token(webhook_secret),
        zoom_account_id=account_id,
        configured_by_user_id=user.id,
        is_active=True,
    )
    credential = TenantCredential(
        tenant_id=tenant.id,
        provider="zoom_phone",
        encrypted_access_token=encrypt_token("old-access"),
        encrypted_refresh_token=encrypt_token("old-refresh"),
        token_expires_at=datetime.now(timezone.utc)
        + (timedelta(minutes=-5) if expired else timedelta(hours=1)),
        scopes="phone:read:list_call_logs:admin phone:read:call_log:admin",
        service_account_email=account_id,
        is_active=True,
    )
    db.add_all([app, credential])
    await db.commit()
    return app, credential


def _signed_headers(secret: str, body: bytes) -> dict[str, str]:
    timestamp = str(int(time.time()))
    digest = hmac.new(
        secret.encode(),
        b"v0:" + timestamp.encode() + b":" + body,
        hashlib.sha256,
    ).hexdigest()
    return {
        "content-type": "application/json",
        "x-zm-request-timestamp": timestamp,
        "x-zm-signature": f"v0={digest}",
    }


def _v3_event(*, account_id="zoom-account-1", direction=None):
    call = {
        "call_element_id": "element-1",
        "call_history_uuid": "history-1",
        "caller_name": "Must not enter durable payload",
    }
    if direction is not None:
        call["direction"] = direction
    return {
        "event": "phone.callee_call_element_completed",
        "payload": {
            "account_id": account_id,
            "object": {"call_elements": [call]},
        },
    }


async def _seed_zoom_phone_oauth_state(
    redis,
    tenant,
    user,
    *,
    account_id="configured-account-123",
    client_id="zoom-client",
) -> str:
    state = secrets.token_urlsafe(24)
    await redis.setex(f"integration:state:{state}", 600, "1")
    await redis.setex(
        f"integration:statedata:{state}",
        600,
        json.dumps(
            {
                "intent": "admin",
                "provider": "zoom_phone",
                "user_id": str(user.id),
                "tenant_id": str(tenant.id),
                "role": "admin",
                "oauth_app_source": "tenant",
                "zoom_account_id": account_id,
                "oauth_client_id_fingerprint": hashlib.sha256(
                    client_id.encode("utf-8")
                ).hexdigest(),
            }
        ),
    )
    return state


def test_v3_element_without_history_uuid_is_left_for_reconciliation():
    event = _v3_event(direction="inbound")
    del event["payload"]["object"]["call_elements"][0]["call_history_uuid"]
    assert zoom_phone_webhook_jobs(event) == []


@pytest.mark.asyncio
async def test_zoom_callback_uses_configured_account_without_user_scope_and_rejects_mismatch(
    client,
    db_session,
    test_redis,
    test_tenant,
    test_user,
    monkeypatch,
):
    _app, credential = await _configure_zoom(
        db_session,
        test_tenant,
        test_user,
        account_id="configured-account-123",
    )
    token_payload = {
        "access_token": "callback-access",
        "refresh_token": "callback-refresh",
        "expires_in": 3600,
        "scope": "phone:read:list_call_logs:admin phone:read:call_log:admin",
    }
    event = _v3_event(account_id="configured-account-123", direction="inbound")
    proof_job = zoom_phone_webhook_jobs(event)[0]
    failed_proof = DurableJob(
        tenant_id=test_tenant.id,
        kind="zoom_phone_call_import",
        idempotency_key=proof_job.idempotency_key,
        payload={
            **proof_job.payload,
            "account_verification": {
                "account_id": "configured-account-123",
                "proof": "signed_v3_call_element",
            },
        },
        status="failed",
        attempts=5,
        max_attempts=5,
        last_error="Previous grant could not access the signed call.",
    )
    db_session.add(failed_proof)
    await db_session.commit()
    calls: list[tuple[str, str]] = []

    class FakeZoomClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, **_kwargs):
            calls.append(("POST", url))
            return httpx.Response(
                200,
                json=token_payload,
                request=httpx.Request("POST", url),
            )

        async def get(self, url, **_kwargs):
            calls.append(("GET", url))
            raise AssertionError("Zoom callback must not call /v2/users/me")

    monkeypatch.setattr(
        integrations_router.httpx,
        "AsyncClient",
        lambda *_args, **_kwargs: FakeZoomClient(),
    )

    state = await _seed_zoom_phone_oauth_state(test_redis, test_tenant, test_user)
    response = await client.get(
        "/api/integrations/zoom-phone/callback",
        params={"code": "valid-code", "state": state},
    )

    assert response.status_code == 302
    await db_session.refresh(credential)
    assert credential.service_account_email == "configured-account-123"
    assert decrypt_token(credential.encrypted_access_token) == "callback-access"
    assert credential.health == "account_verification_required"
    await db_session.refresh(failed_proof)
    assert failed_proof.status == "pending"
    assert failed_proof.attempts == 0
    assert failed_proof.last_error is None
    assert calls == [("POST", "https://zoom.us/oauth/token")]

    pending_status = await client.get("/api/integrations/zoom-phone/status")
    assert pending_status.json()["connected"] is False
    assert pending_status.json()["account_verification_required"] is True
    assert pending_status.json()["reconnect_required"] is False

    body = json.dumps(event, separators=(",", ":")).encode()
    verified = await client.post(
        f"/api/integrations/zoom-phone/webhook/{test_tenant.id}",
        content=body,
        headers=_signed_headers("zoom-webhook-secret", body),
    )
    assert verified.status_code == 200, verified.text
    await db_session.refresh(credential)
    assert credential.health == "account_verification_required"
    queued = await db_session.scalar(
        select(DurableJob).where(DurableJob.tenant_id == test_tenant.id)
    )
    assert queued.payload["account_verification"] == {
        "account_id": "configured-account-123",
        "proof": "signed_v3_call_element",
    }

    stale_state = await _seed_zoom_phone_oauth_state(
        test_redis,
        test_tenant,
        test_user,
        account_id="stale-account-000",
    )
    stale = await client.get(
        "/api/integrations/zoom-phone/callback",
        params={"code": "stale-code", "state": stale_state},
    )
    assert stale.status_code == 302
    assert "error=app_credentials_changed" in stale.headers["location"]
    assert calls == [("POST", "https://zoom.us/oauth/token")]

    token_payload["access_token"] = "must-not-be-stored"
    token_payload["account_id"] = "different-account-999"
    mismatch_state = await _seed_zoom_phone_oauth_state(
        test_redis, test_tenant, test_user
    )
    mismatch = await client.get(
        "/api/integrations/zoom-phone/callback",
        params={"code": "mismatched-code", "state": mismatch_state},
    )

    assert mismatch.status_code == 302
    assert "error=account_mapping_mismatch" in mismatch.headers["location"]
    await db_session.refresh(credential)
    assert decrypt_token(credential.encrypted_access_token) == "callback-access"


@pytest.mark.asyncio
async def test_zoom_app_partial_update_preserves_account_and_change_revokes_grant(
    client, db_session, test_tenant, test_user
):
    app, credential = await _configure_zoom(
        db_session,
        test_tenant,
        test_user,
        account_id="original-account-123",
    )

    secret_only = await client.put(
        "/api/integrations/zoom-phone/app-credentials",
        json={"webhook_secret_token": "rotated-webhook-secret"},
    )
    assert secret_only.status_code == 200, secret_only.text
    await db_session.refresh(app)
    await db_session.refresh(credential)
    assert app.zoom_account_id == "original-account-123"
    assert credential.is_active is True

    account_change = await client.put(
        "/api/integrations/zoom-phone/app-credentials",
        json={"zoom_account_id": "replacement-account-456"},
    )
    assert account_change.status_code == 200, account_change.text
    assert account_change.json()["app_credentials"]["zoom_account_id"] == (
        "replacement-account-456"
    )
    await db_session.refresh(app)
    await db_session.refresh(credential)
    assert app.zoom_account_id == "replacement-account-456"
    assert credential.is_active is False
    assert credential.health == "reauthorization_required"


@pytest.mark.asyncio
async def test_new_zoom_app_requires_and_persists_explicit_account_id(
    client, db_session, test_tenant
):
    payload = {
        "client_id": "new-client",
        "client_secret": "new-secret",
        "webhook_secret_token": "new-webhook-secret",
    }
    missing = await client.put(
        "/api/integrations/zoom-phone/app-credentials",
        json=payload,
    )
    assert missing.status_code == 422
    assert missing.json()["detail"] == "Zoom Account ID is required."

    saved = await client.put(
        "/api/integrations/zoom-phone/app-credentials",
        json={**payload, "zoom_account_id": "explicit-account-123"},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["app_credentials"]["zoom_account_id"] == (
        "explicit-account-123"
    )
    app = await db_session.scalar(
        select(TenantOAuthApp).where(TenantOAuthApp.tenant_id == test_tenant.id)
    )
    assert app.zoom_account_id == "explicit-account-123"


@pytest.mark.asyncio
async def test_zoom_app_rejects_malformed_or_too_short_account_id(
    client, db_session, test_tenant, test_user
):
    _app, credential = await _configure_zoom(db_session, test_tenant, test_user)

    malformed = await client.put(
        "/api/integrations/zoom-phone/app-credentials",
        json={"zoom_account_id": "bad/account"},
    )
    too_short = await client.put(
        "/api/integrations/zoom-phone/app-credentials",
        json={"zoom_account_id": "short"},
    )

    assert malformed.status_code == 422
    assert too_short.status_code == 422
    await db_session.refresh(credential)
    assert credential.is_active is True


@pytest.mark.asyncio
async def test_signed_v3_webhook_commits_one_minimal_job_before_ack_and_dedupes(
    client, db_session, test_engine, test_tenant, test_user, monkeypatch
):
    await _configure_zoom(db_session, test_tenant, test_user)

    async def network_must_not_run(*_args, **_kwargs):
        raise AssertionError("webhook ingress must not call Zoom")

    monkeypatch.setattr(httpx.AsyncClient, "get", network_must_not_run)
    event = _v3_event(direction=None)
    body = json.dumps(event, separators=(",", ":")).encode()
    url = f"/api/integrations/zoom-phone/webhook/{test_tenant.id}"

    first = await client.post(
        url,
        content=body,
        headers=_signed_headers("zoom-webhook-secret", body),
    )
    second = await client.post(
        url,
        content=body,
        headers=_signed_headers("zoom-webhook-secret", body),
    )

    assert first.status_code == 200, first.text
    assert first.json() == {"status": "accepted", "queued": 1}
    assert second.status_code == 200
    sessions = async_sessionmaker(test_engine, expire_on_commit=False)
    async with sessions() as committed_db:
        await set_tenant_context(committed_db, str(test_tenant.id))
        jobs = list(
            (
                await committed_db.scalars(
                    select(DurableJob).where(
                        DurableJob.tenant_id == test_tenant.id,
                        DurableJob.kind == "zoom_phone_call_import",
                    )
                )
            ).all()
        )
    assert len(jobs) == 1
    assert jobs[0].status == "pending"
    assert jobs[0].payload == {
        "event_name": "phone.callee_call_element_completed",
        "call_history_id": "history-1",
        "call_element_id": "element-1",
        "stable_call_id": "history-1",
    }
    assert "caller_name" not in json.dumps(jobs[0].payload)


@pytest.mark.asyncio
async def test_webhook_rejects_account_mismatch_and_shared_route(
    client, db_session, test_tenant, test_user
):
    await _configure_zoom(db_session, test_tenant, test_user)
    event = _v3_event(account_id="different-account", direction="inbound")
    body = json.dumps(event, separators=(",", ":")).encode()
    response = await client.post(
        f"/api/integrations/zoom-phone/webhook/{test_tenant.id}",
        content=body,
        headers=_signed_headers("zoom-webhook-secret", body),
    )
    assert response.status_code == 403
    assert await db_session.scalar(select(func.count(DurableJob.id))) == 0

    shared = await client.post("/api/integrations/zoom-phone/webhook", json=event)
    assert shared.status_code == 410


@pytest.mark.asyncio
async def test_webhook_and_status_reject_app_grant_mapping_mismatch(
    client, db_session, test_tenant, test_user
):
    app, _credential = await _configure_zoom(
        db_session,
        test_tenant,
        test_user,
        account_id="credential-account-123",
    )
    app.zoom_account_id = "different-app-account-456"
    await db_session.commit()

    status = await client.get("/api/integrations/zoom-phone/status")
    assert status.status_code == 200
    assert status.json()["connected"] is False
    assert status.json()["reconnect_required"] is True

    event = _v3_event(account_id="credential-account-123", direction="inbound")
    body = json.dumps(event, separators=(",", ":")).encode()
    response = await client.post(
        f"/api/integrations/zoom-phone/webhook/{test_tenant.id}",
        content=body,
        headers=_signed_headers("zoom-webhook-secret", body),
    )
    assert response.status_code == 409
    assert await db_session.scalar(select(func.count(DurableJob.id))) == 0


@pytest.mark.asyncio
async def test_pending_account_exact_v3_fetch_promotes_and_imports(
    client, db_session, test_tenant, test_user, monkeypatch
):
    _app, credential = await _configure_zoom(db_session, test_tenant, test_user)
    credential.health = "account_verification_required"
    await db_session.commit()
    event = _v3_event(direction="inbound")
    body = json.dumps(event, separators=(",", ":")).encode()
    accepted = await client.post(
        f"/api/integrations/zoom-phone/webhook/{test_tenant.id}",
        content=body,
        headers=_signed_headers("zoom-webhook-secret", body),
    )
    assert accepted.status_code == 200, accepted.text
    job = await db_session.scalar(select(DurableJob))
    job_id = job.id
    credential_id = credential.id
    assert credential.health == "account_verification_required"

    async def exact_detail(self, url, *args, **kwargs):
        assert url.endswith("/phone/call_element/element-1")
        return httpx.Response(
            200,
            json={
                "call_element_id": "element-1",
                "call_history_uuid": "history-1",
                "direction": "inbound",
                "caller_number": "+1 701-555-0100",
            },
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", exact_detail)
    assert await process_job(job_id, test_tenant.id)
    await db_session.rollback()
    db_session.expire_all()
    saved_credential = await db_session.get(TenantCredential, credential_id)
    assert saved_credential.health == "healthy"
    assert await db_session.scalar(select(func.count(CommunicationLog.id))) == 1


@pytest.mark.asyncio
async def test_wrong_account_exact_fetch_never_promotes_or_imports(
    client, db_session, test_tenant, test_user, monkeypatch
):
    _app, credential = await _configure_zoom(db_session, test_tenant, test_user)
    credential.health = "account_verification_required"
    await db_session.commit()
    event = _v3_event(direction="inbound")
    body = json.dumps(event, separators=(",", ":")).encode()
    accepted = await client.post(
        f"/api/integrations/zoom-phone/webhook/{test_tenant.id}",
        content=body,
        headers=_signed_headers("zoom-webhook-secret", body),
    )
    assert accepted.status_code == 200, accepted.text
    job = await db_session.scalar(select(DurableJob))
    job_id = job.id
    credential_id = credential.id

    async def wrong_account(self, url, *args, **kwargs):
        return httpx.Response(
            403,
            json={"code": 300, "message": "Call element not available"},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", wrong_account)
    assert await process_job(job_id, test_tenant.id)
    await db_session.rollback()
    db_session.expire_all()
    saved_credential = await db_session.get(TenantCredential, credential_id)
    saved_job = await db_session.get(DurableJob, job_id)
    assert saved_credential.health == "account_verification_required"
    assert saved_job.status == "failed"
    assert await db_session.scalar(select(func.count(CommunicationLog.id))) == 0


@pytest.mark.asyncio
async def test_pending_account_rejects_v2_proof_and_oversize_body(
    client, db_session, test_tenant, test_user
):
    _app, credential = await _configure_zoom(db_session, test_tenant, test_user)
    credential.health = "account_verification_required"
    await db_session.commit()
    v2_event = {
        "event": "phone.callee_call_history_completed",
        "payload": {
            "account_id": "zoom-account-1",
            "object": {"call_logs": [{"id": "history-1", "direction": "inbound"}]},
        },
    }
    v2_body = json.dumps(v2_event, separators=(",", ":")).encode()
    rejected = await client.post(
        f"/api/integrations/zoom-phone/webhook/{test_tenant.id}",
        content=v2_body,
        headers=_signed_headers("zoom-webhook-secret", v2_body),
    )
    assert rejected.status_code == 409
    assert await db_session.scalar(select(func.count(DurableJob.id))) == 0

    oversized = await client.post(
        f"/api/integrations/zoom-phone/webhook/{test_tenant.id}",
        content=b"x" * (integrations_router.ZOOM_WEBHOOK_MAX_BODY_BYTES + 1),
        headers={"content-length": "1"},
    )
    assert oversized.status_code == 413


@pytest.mark.asyncio
async def test_crc_and_invalid_signature_never_enqueue(
    client, db_session, test_tenant, test_user
):
    await _configure_zoom(db_session, test_tenant, test_user)
    url = f"/api/integrations/zoom-phone/webhook/{test_tenant.id}"

    crc = await client.post(
        url,
        json={
            "event": "endpoint.url_validation",
            "payload": {"plainToken": "crc-only"},
        },
    )
    assert crc.status_code == 200
    assert crc.json()["plainToken"] == "crc-only"

    event = _v3_event(direction="inbound")
    body = json.dumps(event, separators=(",", ":")).encode()
    rejected = await client.post(
        url,
        content=body,
        headers={
            "content-type": "application/json",
            "x-zm-request-timestamp": str(int(time.time())),
            "x-zm-signature": "v0=invalid",
        },
    )
    assert rejected.status_code == 401
    assert await db_session.scalar(select(func.count(DurableJob.id))) == 0


@pytest.mark.asyncio
async def test_commit_failure_is_not_acknowledged_and_job_is_not_durable(
    client, db_session, test_engine, test_tenant, test_user, monkeypatch
):
    await _configure_zoom(db_session, test_tenant, test_user)
    tenant_id = test_tenant.id
    event = _v3_event(direction="inbound")
    body = json.dumps(event, separators=(",", ":")).encode()

    async def fail_commit(_self):
        raise RuntimeError("database commit failed")

    monkeypatch.setattr(type(db_session), "commit", fail_commit)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application, raise_app_exceptions=False),
        base_url="http://test",
        headers=dict(client.headers),
    ) as non_raising_client:
        response = await non_raising_client.post(
            f"/api/integrations/zoom-phone/webhook/{tenant_id}",
            content=body,
            headers=_signed_headers("zoom-webhook-secret", body),
        )
    assert response.status_code == 500
    await db_session.rollback()

    sessions = async_sessionmaker(test_engine, expire_on_commit=False)
    async with sessions() as check_db:
        await set_tenant_context(check_db, str(tenant_id))
        assert await check_db.scalar(select(func.count(DurableJob.id))) == 0


@pytest.mark.asyncio
async def test_same_provider_element_is_idempotent_per_tenant(
    client, db_session, test_tenant, test_user
):
    await _configure_zoom(db_session, test_tenant, test_user)
    other_tenant = Tenant(
        id=uuid.uuid4(),
        name="Other Zoom firm",
        domain="other-zoom-webhook.test",
        billing_tier="payg",
        is_active=True,
    )
    db_session.add(other_tenant)
    await db_session.flush()
    db_session.add_all(
        [
            TenantOAuthApp(
                tenant_id=other_tenant.id,
                provider="zoom_phone",
                encrypted_client_id=encrypt_token("other-client"),
                encrypted_client_secret=encrypt_token("other-secret"),
                encrypted_webhook_secret_token=encrypt_token("other-webhook"),
                zoom_account_id="other-account",
                is_active=True,
            ),
            TenantCredential(
                tenant_id=other_tenant.id,
                provider="zoom_phone",
                encrypted_access_token=encrypt_token("other-access"),
                encrypted_refresh_token=encrypt_token("other-refresh"),
                token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                scopes="phone:read:list_call_logs:admin phone:read:call_log:admin",
                service_account_email="other-account",
                is_active=True,
            ),
        ]
    )
    await db_session.commit()

    first_event = _v3_event(account_id="zoom-account-1", direction="inbound")
    first_body = json.dumps(first_event, separators=(",", ":")).encode()
    first = await client.post(
        f"/api/integrations/zoom-phone/webhook/{test_tenant.id}",
        content=first_body,
        headers=_signed_headers("zoom-webhook-secret", first_body),
    )
    second_event = _v3_event(account_id="other-account", direction="inbound")
    second_body = json.dumps(second_event, separators=(",", ":")).encode()
    second = await client.post(
        f"/api/integrations/zoom-phone/webhook/{other_tenant.id}",
        content=second_body,
        headers=_signed_headers("other-webhook", second_body),
    )
    assert first.status_code == second.status_code == 200
    jobs = list(
        (
            await db_session.scalars(
                select(DurableJob).where(DurableJob.kind == "zoom_phone_call_import")
            )
        ).all()
    )
    assert len(jobs) == 2
    assert {job.tenant_id for job in jobs} == {test_tenant.id, other_tenant.id}
    assert len({job.idempotency_key for job in jobs}) == 1


@pytest.mark.asyncio
async def test_worker_retries_transient_failure_then_completes(
    db_session, test_tenant, test_user, monkeypatch
):
    tenant_id = test_tenant.id
    await _configure_zoom(db_session, test_tenant, test_user)
    job = await enqueue_job(
        db_session,
        tenant_id=tenant_id,
        kind="zoom_phone_call_import",
        idempotency_key="retry-call",
        payload={
            "event_name": "phone.callee_call_history_completed",
            "call_history_id": "history-retry",
            "call_element_id": None,
            "stable_call_id": "history-retry",
        },
    )
    await db_session.commit()
    job_id = job.id
    calls = 0

    async def flaky_import(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ZoomPhoneIntegrationError("temporary Zoom outage")
        return ZoomPhoneImportResult(imported=1)

    monkeypatch.setattr(
        "app.services.zoom_phone.import_zoom_phone_webhook_job", flaky_import
    )
    assert await process_job(job_id, tenant_id)
    await db_session.rollback()
    db_session.expire_all()
    saved = await db_session.get(DurableJob, job_id)
    assert saved.status == "pending"
    assert saved.attempts == 1
    assert "temporary Zoom outage" in saved.last_error

    saved.available_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await db_session.commit()
    assert await process_job(job_id, tenant_id)
    await db_session.rollback()
    db_session.expire_all()
    saved = await db_session.get(DurableJob, job_id)
    assert saved.status == "completed"
    assert saved.attempts == 2
    assert saved.result["imported"] == 1


@pytest.mark.asyncio
async def test_terminal_redelivery_is_accepted_and_reconcile_repairs_only_match(
    client, db_session, test_tenant, test_user, monkeypatch
):
    await _configure_zoom(db_session, test_tenant, test_user)
    event = _v3_event(direction="inbound")
    body = json.dumps(event, separators=(",", ":")).encode()
    url = f"/api/integrations/zoom-phone/webhook/{test_tenant.id}"
    first = await client.post(
        url,
        content=body,
        headers=_signed_headers("zoom-webhook-secret", body),
    )
    assert first.status_code == 200
    target = await db_session.scalar(
        select(DurableJob).where(
            DurableJob.tenant_id == test_tenant.id,
            DurableJob.kind == "zoom_phone_call_import",
        )
    )
    target.status = "failed"
    target.attempts = target.max_attempts
    target.last_error = "permanent detail failure"
    await db_session.commit()
    target_id = target.id

    redelivery = await client.post(
        url,
        content=body,
        headers=_signed_headers("zoom-webhook-secret", body),
    )
    assert redelivery.status_code == 200
    assert redelivery.json() == {
        "status": "accepted",
        "queued": 0,
        "reconciliation_pending": 1,
    }
    await db_session.refresh(target)
    assert target.status == "failed"

    unmatched = DurableJob(
        tenant_id=test_tenant.id,
        kind="zoom_phone_call_import",
        idempotency_key="unmatched-terminal",
        payload={
            "event_name": "phone.callee_call_element_completed",
            "call_history_id": "history-unmatched",
            "call_element_id": "element-unmatched",
            "stable_call_id": "history-unmatched",
        },
        status="failed",
        attempts=5,
        max_attempts=5,
        last_error="unmatched",
    )
    unrelated_meter = DurableJob(
        tenant_id=test_tenant.id,
        kind="mcp_stripe_meter",
        idempotency_key="unrelated-meter-terminal",
        payload={"event_id": "unrelated"},
        status="failed",
        attempts=5,
        max_attempts=5,
        last_error="unrelated",
    )
    unrelated_general = DurableJob(
        tenant_id=test_tenant.id,
        kind="document_ingest",
        idempotency_key="unrelated-document-terminal",
        payload={"document_id": str(uuid.uuid4())},
        status="failed",
        attempts=5,
        max_attempts=5,
        last_error="unrelated",
    )
    db_session.add_all(
        [
            CommunicationLog(
                tenant_id=test_tenant.id,
                direction="inbound",
                channel="call",
                status="received",
                subject="Reconciled call",
                external_ref="zoom_phone:call:history-1",
                participants={"provider": "zoom_phone"},
            ),
            unmatched,
            unrelated_meter,
            unrelated_general,
        ]
    )
    reconcile = await enqueue_job(
        db_session,
        tenant_id=test_tenant.id,
        kind="zoom_phone_reconcile",
        idempotency_key="targeted-reconcile",
        payload={"days": 1},
    )
    await db_session.commit()
    unrelated_ids = {unmatched.id, unrelated_meter.id, unrelated_general.id}

    async def successful_sync(*_args, **_kwargs):
        return ZoomPhoneImportResult(skipped=1)

    monkeypatch.setattr(
        "app.services.zoom_phone.sync_zoom_phone_call_history",
        successful_sync,
    )
    assert await process_job(reconcile.id, test_tenant.id)
    await db_session.rollback()
    db_session.expire_all()
    repaired = await db_session.get(DurableJob, target_id)
    assert repaired.status == "completed"
    assert repaired.result == {"reconciled": True}
    for unrelated_id in unrelated_ids:
        unrelated = await db_session.get(DurableJob, unrelated_id)
        assert unrelated.status == "failed"


@pytest.mark.asyncio
async def test_rotating_refresh_commits_before_downstream_detail_failure(
    db_session, test_tenant, test_user, monkeypatch
):
    _, credential = await _configure_zoom(
        db_session, test_tenant, test_user, expired=True
    )
    credential_id = credential.id
    posts = 0

    async def fake_post(self, url, *args, **kwargs):
        nonlocal posts
        posts += 1
        await asyncio.sleep(0.05)
        return httpx.Response(
            200,
            json={
                "access_token": "new-access",
                "refresh_token": "new-refresh",
                "expires_in": 3600,
            },
            request=httpx.Request("POST", url),
        )

    async def failing_get(self, url, *args, **kwargs):
        return httpx.Response(
            503,
            json={"message": "temporary"},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    monkeypatch.setattr(httpx.AsyncClient, "get", failing_get)

    tokens = await asyncio.gather(
        get_zoom_phone_token(db_session, str(test_tenant.id)),
        get_zoom_phone_token(db_session, str(test_tenant.id)),
    )
    assert tokens == ["new-access", "new-access"]
    assert posts == 1
    with pytest.raises(ZoomPhoneIntegrationError):
        await fetch_zoom_phone_call_history_detail(
            db_session,
            tenant_id=str(test_tenant.id),
            call_history_id="history-1",
        )

    await db_session.rollback()
    db_session.expire_all()
    saved = await db_session.get(TenantCredential, credential_id)
    assert decrypt_token(saved.encrypted_access_token) == "new-access"
    assert decrypt_token(saved.encrypted_refresh_token) == "new-refresh"
    assert saved.last_refresh_at is not None


@pytest.mark.asyncio
async def test_rejected_cached_token_forces_one_persisted_refresh_and_retry(
    db_session, test_tenant, test_user, monkeypatch
):
    _, credential = await _configure_zoom(db_session, test_tenant, test_user)
    credential_id = credential.id
    posts = 0
    auth_headers = []

    async def fake_post(self, url, *args, **kwargs):
        nonlocal posts
        posts += 1
        return httpx.Response(
            200,
            json={
                "access_token": "refreshed-access",
                "refresh_token": "refreshed-refresh",
                "expires_in": 3600,
            },
            request=httpx.Request("POST", url),
        )

    async def fake_get(self, url, *args, **kwargs):
        auth_headers.append(kwargs["headers"]["Authorization"])
        if len(auth_headers) == 1:
            return httpx.Response(
                401,
                json={"code": 124, "message": "Access token has expired."},
                request=httpx.Request("GET", url),
            )
        return httpx.Response(
            200,
            json={
                "call_history_uuid": "history-401",
                "direction": "inbound",
            },
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    detail = await fetch_zoom_phone_call_history_detail(
        db_session,
        tenant_id=str(test_tenant.id),
        call_history_id="history-401",
    )
    assert detail["call_history_uuid"] == "history-401"
    assert posts == 1
    assert auth_headers == ["Bearer old-access", "Bearer refreshed-access"]
    await db_session.rollback()
    db_session.expire_all()
    saved = await db_session.get(TenantCredential, credential_id)
    assert decrypt_token(saved.encrypted_refresh_token) == "refreshed-refresh"


@pytest.mark.asyncio
async def test_concurrent_cached_401_responses_rotate_refresh_token_once(
    db_session, test_tenant, test_user, monkeypatch
):
    await _configure_zoom(db_session, test_tenant, test_user)
    posts = 0
    old_requests = 0
    both_old_requested = asyncio.Event()

    async def fake_post(self, url, *args, **kwargs):
        nonlocal posts
        posts += 1
        await asyncio.sleep(0.05)
        return httpx.Response(
            200,
            json={
                "access_token": "one-rotated-access",
                "refresh_token": "one-rotated-refresh",
                "expires_in": 3600,
            },
            request=httpx.Request("POST", url),
        )

    async def fake_get(self, url, *args, **kwargs):
        nonlocal old_requests
        authorization = kwargs["headers"]["Authorization"]
        if authorization == "Bearer old-access":
            old_requests += 1
            if old_requests == 2:
                both_old_requested.set()
            await both_old_requested.wait()
            return httpx.Response(
                401,
                json={"code": 124, "message": "Access token has expired."},
                request=httpx.Request("GET", url),
            )
        return httpx.Response(
            200,
            json={
                "call_history_uuid": "history-concurrent-401",
                "direction": "inbound",
            },
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    results = await asyncio.gather(
        fetch_zoom_phone_call_history_detail(
            db_session,
            tenant_id=str(test_tenant.id),
            call_history_id="history-concurrent-401",
        ),
        fetch_zoom_phone_call_history_detail(
            db_session,
            tenant_id=str(test_tenant.id),
            call_history_id="history-concurrent-401",
        ),
    )
    assert len(results) == 2
    assert old_requests == 2
    assert posts == 1


@pytest.mark.asyncio
async def test_zoom_code_104_marks_missing_scopes_and_blocks_more_provider_calls(
    client, db_session, test_tenant, test_user, monkeypatch
):
    await _configure_zoom(db_session, test_tenant, test_user)
    tenant_id = test_tenant.id
    provider_calls = 0
    original_http_get = httpx.AsyncClient.get

    async def missing_scope(self, url, *args, **kwargs):
        nonlocal provider_calls
        provider_calls += 1
        return httpx.Response(
            400,
            json={"code": 104, "message": "Invalid access token scope."},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", missing_scope)
    with pytest.raises(ZoomPhonePermanentError):
        await fetch_zoom_phone_call_history_detail(
            db_session,
            tenant_id=str(tenant_id),
            call_history_id="scope-rejected",
        )
    await db_session.rollback()
    db_session.expire_all()

    monkeypatch.setattr(httpx.AsyncClient, "get", original_http_get)
    status = await client.get("/api/integrations/zoom-phone/status")
    assert status.status_code == 200, status.text
    payload = status.json()
    assert payload["connected"] is False
    assert payload["status"] == "missing_scopes"
    assert payload["health"] == "missing_scopes"
    assert payload["reconnect_required"] is True
    assert payload["missing_scopes"]

    job = await enqueue_job(
        db_session,
        tenant_id=tenant_id,
        kind="zoom_phone_call_import",
        idempotency_key="scope-blocked-job",
        payload={
            "event_name": "phone.callee_call_element_completed",
            "call_history_id": "scope-rejected",
            "call_element_id": "scope-element",
            "stable_call_id": "scope-rejected",
        },
    )
    await db_session.commit()
    job_id = job.id

    async def unexpected_provider_get(*_args, **_kwargs):
        raise AssertionError("missing-scope grant must not call Zoom again")

    monkeypatch.setattr(httpx.AsyncClient, "get", unexpected_provider_get)
    assert await process_job(job_id, tenant_id)
    await db_session.rollback()
    db_session.expire_all()
    saved = await db_session.get(DurableJob, job_id)
    assert saved.status == "failed"
    assert provider_calls == 1


@pytest.mark.asyncio
async def test_invalid_client_deactivates_grant_and_later_calls_fail_fast(
    db_session, test_tenant, test_user, monkeypatch
):
    _, credential = await _configure_zoom(
        db_session, test_tenant, test_user, expired=True
    )
    credential_id = credential.id
    posts = 0

    async def invalid_client(self, url, *args, **kwargs):
        nonlocal posts
        posts += 1
        return httpx.Response(
            400,
            json={"error": "invalid_client", "reason": "bad client secret"},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", invalid_client)
    with pytest.raises(ZoomPhoneReauthorizationRequired):
        await get_zoom_phone_token(db_session, str(test_tenant.id))
    assert await get_zoom_phone_token(db_session, str(test_tenant.id)) is None
    assert posts == 1
    await db_session.rollback()
    db_session.expire_all()
    saved = await db_session.get(TenantCredential, credential_id)
    assert saved.is_active is False
    assert saved.health == "reauthorization_required"


@pytest.mark.asyncio
async def test_cached_token_fails_closed_when_app_and_grant_accounts_diverge(
    db_session, test_tenant, test_user, monkeypatch
):
    app, credential = await _configure_zoom(db_session, test_tenant, test_user)
    credential_id = credential.id
    app.zoom_account_id = "different-account-456"
    await db_session.commit()

    async def unexpected_provider_call(*_args, **_kwargs):
        raise AssertionError("mismatched account mapping must not call Zoom")

    monkeypatch.setattr(httpx.AsyncClient, "post", unexpected_provider_call)
    with pytest.raises(ZoomPhoneReauthorizationRequired, match="configured Zoom"):
        await get_zoom_phone_token(db_session, str(test_tenant.id))

    await db_session.rollback()
    db_session.expire_all()
    saved = await db_session.get(TenantCredential, credential_id)
    assert saved.is_active is False
    assert saved.health == "reauthorization_required"


@pytest.mark.asyncio
async def test_unverified_account_grant_cannot_call_zoom_until_signed_webhook(
    db_session, test_tenant, test_user, monkeypatch
):
    _app, credential = await _configure_zoom(db_session, test_tenant, test_user)
    credential.health = "account_verification_required"
    await db_session.commit()

    async def unexpected_provider_call(*_args, **_kwargs):
        raise AssertionError("unverified account mapping must not call Zoom")

    monkeypatch.setattr(httpx.AsyncClient, "post", unexpected_provider_call)
    with pytest.raises(ZoomPhoneReauthorizationRequired, match="signed webhook"):
        await get_zoom_phone_token(db_session, str(test_tenant.id))

    await db_session.refresh(credential)
    assert credential.is_active is True
    assert credential.health == "account_verification_required"


@pytest.mark.asyncio
async def test_numeric_invalid_client_refresh_error_requires_reauthorization(
    db_session, test_tenant, test_user, monkeypatch
):
    _, credential = await _configure_zoom(
        db_session, test_tenant, test_user, expired=True
    )
    credential_id = credential.id

    async def numeric_invalid_client(self, url, *args, **kwargs):
        return httpx.Response(
            400,
            json={"code": 4702},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", numeric_invalid_client)
    with pytest.raises(ZoomPhoneReauthorizationRequired):
        await get_zoom_phone_token(db_session, str(test_tenant.id))
    await db_session.rollback()
    db_session.expire_all()
    saved = await db_session.get(TenantCredential, credential_id)
    assert saved.is_active is False
    assert saved.health == "reauthorization_required"


@pytest.mark.asyncio
async def test_repeated_zoom_history_page_token_stops_pagination(
    db_session, test_tenant, test_user, monkeypatch
):
    await _configure_zoom(db_session, test_tenant, test_user)
    calls = 0

    async def repeated_page(self, url, *args, **kwargs):
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={"call_history": [], "next_page_token": "same-token"},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", repeated_page)
    with pytest.raises(ZoomPhoneIntegrationError, match="repeated"):
        await fetch_zoom_phone_call_history(
            db_session,
            tenant_id=str(test_tenant.id),
            days=1,
        )
    assert calls == 2


@pytest.mark.asyncio
async def test_inactive_zoom_job_never_calls_provider_and_cross_tenant_has_no_fallback(
    db_session, test_tenant, test_user, monkeypatch
):
    await _configure_zoom(db_session, test_tenant, test_user)
    other = Tenant(
        id=uuid.uuid4(),
        name="Other firm",
        domain="other-zoom.test",
        billing_tier="payg",
        is_active=True,
    )
    db_session.add(other)
    await db_session.commit()

    async def unexpected_post(*_args, **_kwargs):
        raise AssertionError("tenant without a grant must not use global Zoom")

    monkeypatch.setattr(httpx.AsyncClient, "post", unexpected_post)
    assert await get_zoom_phone_token(db_session, str(other.id)) is None

    job = await enqueue_job(
        db_session,
        tenant_id=test_tenant.id,
        kind="zoom_phone_call_import",
        idempotency_key="inactive-call",
        payload={
            "event_name": "phone.callee_call_history_completed",
            "call_history_id": "inactive-call",
            "call_element_id": None,
            "stable_call_id": "inactive-call",
        },
    )
    test_tenant.is_active = False
    await db_session.commit()
    job_id = job.id
    assert await process_job(job_id, test_tenant.id)
    await db_session.rollback()
    db_session.expire_all()
    saved = await db_session.get(DurableJob, job_id)
    assert saved.status == "completed"
    assert saved.result == {"ignored": "inactive_tenant"}


@pytest.mark.asyncio
async def test_inactive_tenant_drains_meter_but_other_jobs_have_no_egress(
    db_session, test_tenant, monkeypatch
):
    general = await enqueue_job(
        db_session,
        tenant_id=test_tenant.id,
        kind="document_ingest",
        idempotency_key="inactive-general",
        payload={"document_id": str(uuid.uuid4())},
    )
    zoom = await enqueue_job(
        db_session,
        tenant_id=test_tenant.id,
        kind="zoom_phone_call_import",
        idempotency_key="inactive-zoom-lane",
        payload={
            "event_name": "phone.callee_call_element_completed",
            "call_history_id": "inactive-history",
            "call_element_id": "inactive-element",
            "stable_call_id": "inactive-history",
        },
    )
    meter = await enqueue_job(
        db_session,
        tenant_id=test_tenant.id,
        kind="mcp_stripe_meter",
        idempotency_key="inactive-meter",
        payload={"event_id": "meter-after-deactivation"},
    )
    general_id = general.id
    zoom_id = zoom.id
    meter_id = meter.id
    test_tenant.is_active = False
    await db_session.commit()
    delivered: list[dict] = []

    async def unexpected_document(_row):
        raise AssertionError("inactive document job must not perform egress")

    monkeypatch.setattr(
        "app.services.durable_job_worker._run_document_ingest",
        unexpected_document,
    )

    async def deliver_meter(payload):
        delivered.append(payload)
        return {"delivered": True}

    monkeypatch.setattr(
        "app.services.mcp_product.deliver_mcp_meter_event",
        deliver_meter,
    )
    assert await process_job(general_id, test_tenant.id)
    assert await process_job(meter_id, test_tenant.id)
    assert await process_job(zoom_id, test_tenant.id)
    await db_session.rollback()
    db_session.expire_all()

    general_saved = await db_session.get(DurableJob, general_id)
    meter_saved = await db_session.get(DurableJob, meter_id)
    zoom_saved = await db_session.get(DurableJob, zoom_id)
    assert general_saved.status == "completed"
    assert general_saved.result == {"ignored": "inactive_tenant"}
    assert meter_saved.status == "completed"
    assert meter_saved.result == {"delivered": True}
    assert zoom_saved.status == "completed"
    assert zoom_saved.result == {"ignored": "inactive_tenant"}
    assert delivered == [{"event_id": "meter-after-deactivation"}]


@pytest.mark.asyncio
async def test_atomic_concurrent_import_keeps_one_row_and_preserves_captured_fields(
    test_engine, db_session, test_tenant, test_user
):
    record = {
        "call_history_uuid": "atomic-history",
        "direction": "inbound",
        "caller_name": "Provider Caller",
        "caller_number": "+1 701-555-0100",
        "start_time": "2026-07-10T01:00:00Z",
        "result": "answered",
    }
    sessions = async_sessionmaker(test_engine, expire_on_commit=False)

    async def submit():
        async with sessions() as db:
            await set_tenant_context(db, str(test_tenant.id))
            result = await import_zoom_phone_records(
                db, tenant_id=str(test_tenant.id), records=[record]
            )
            await db.commit()
            return result

    await asyncio.gather(submit(), submit())
    rows = list(
        (
            await db_session.scalars(
                select(CommunicationLog).where(
                    CommunicationLog.external_ref == "zoom_phone:call:atomic-history"
                )
            )
        ).all()
    )
    assert len(rows) == 1
    log = rows[0]
    contact = Contact(
        tenant_id=test_tenant.id,
        contact_type="prospect",
        first_name="Corrected",
        phone="7015550111",
        created_by_user_id=test_user.id,
    )
    db_session.add(contact)
    await db_session.flush()
    log.contact_id = contact.id
    log.created_by_user_id = test_user.id
    log.subject = "Inbound call: Corrected Caller"
    log.summary = "Staff purpose"
    log.body = "Staff notes must survive"
    log.participants = {
        **(log.participants or {}),
        "caller_name": "Corrected Caller",
        "phone": "7015550111",
        "intake_lead_id": "lead-stable",
    }
    await db_session.commit()

    changed = {**record, "result": "missed", "duration": 15}
    result = await import_zoom_phone_records(
        db_session, tenant_id=str(test_tenant.id), records=[changed]
    )
    await db_session.commit()
    assert result.updated == 1
    assert log.subject == "Inbound call: Corrected Caller"
    assert log.summary == "Staff purpose"
    assert log.body == "Staff notes must survive"
    assert log.contact_id == contact.id
    assert log.participants["caller_name"] == "Corrected Caller"
    assert log.participants["phone"] == "7015550111"
    assert log.participants["intake_lead_id"] == "lead-stable"
    assert log.participants["result"] == "missed"


@pytest.mark.asyncio
async def test_history_identity_matches_webhook_and_reconciliation(
    db_session, test_tenant
):
    webhook_detail = {
        "canonical_call_id": "shared-history",
        "call_history_uuid": "shared-history",
        "call_element_id": "element-leg",
        "direction": "inbound",
        "caller_number": "+1 701-555-0100",
    }
    reconciliation_detail = {
        "call_history_uuid": "shared-history",
        "call_element_id": "element-leg",
        "direction": "inbound",
        "caller_number": "+1 701-555-0100",
    }
    await import_zoom_phone_records(
        db_session, tenant_id=str(test_tenant.id), records=[webhook_detail]
    )
    reconciliation = await import_zoom_phone_records(
        db_session, tenant_id=str(test_tenant.id), records=[reconciliation_detail]
    )
    await db_session.commit()
    assert reconciliation.updated + reconciliation.skipped == 1
    assert (
        await db_session.scalar(
            select(func.count(CommunicationLog.id)).where(
                CommunicationLog.tenant_id == test_tenant.id,
                CommunicationLog.external_ref.like("zoom_phone:call:%"),
            )
        )
        == 1
    )
    sole_ref = await db_session.scalar(
        select(CommunicationLog.external_ref).where(
            CommunicationLog.tenant_id == test_tenant.id,
            CommunicationLog.external_ref.like("zoom_phone:call:%"),
        )
    )
    assert sole_ref == "zoom_phone:call:shared-history"


@pytest.mark.asyncio
async def test_distinct_elements_share_one_history_call_without_losing_inbound(
    db_session, test_tenant, monkeypatch
):
    event = {
        "event": "phone.callee_call_element_completed",
        "payload": {
            "account_id": "account",
            "object": {
                "call_elements": [
                    {
                        "call_element_id": "outbound-leg",
                        "call_history_uuid": "shared-call",
                    },
                    {
                        "call_element_id": "inbound-leg",
                        "call_history_uuid": "shared-call",
                    },
                ]
            },
        },
    }
    jobs = zoom_phone_webhook_jobs(event)
    assert len(jobs) == 2
    assert jobs[0].idempotency_key != jobs[1].idempotency_key
    assert {job.payload["stable_call_id"] for job in jobs} == {"shared-call"}

    async def detail(*_args, call_element_id=None, **_kwargs):
        return {
            "call_element_id": call_element_id,
            "call_history_uuid": "shared-call",
            "direction": "inbound" if call_element_id == "inbound-leg" else "outbound",
            "caller_number": "+1 701-555-0100",
        }

    monkeypatch.setattr(
        "app.services.zoom_phone.fetch_zoom_phone_call_history_detail", detail
    )
    results = [
        await import_zoom_phone_webhook_job(
            db_session,
            tenant_id=str(test_tenant.id),
            payload=job.payload,
        )
        for job in jobs
    ]
    await db_session.commit()
    assert sum(item.imported for item in results) == 1
    rows = list(
        (
            await db_session.scalars(
                select(CommunicationLog).where(
                    CommunicationLog.tenant_id == test_tenant.id,
                    CommunicationLog.external_ref == "zoom_phone:call:shared-call",
                )
            )
        ).all()
    )
    assert len(rows) == 1
    assert rows[0].direction == "inbound"


@pytest.mark.asyncio
async def test_reconciliation_ticks_leave_one_outstanding_bucket(
    db_session, test_tenant, test_user
):
    await _configure_zoom(db_session, test_tenant, test_user)
    await enqueue_zoom_phone_reconciliation_jobs()
    await enqueue_zoom_phone_reconciliation_jobs()
    await db_session.rollback()
    jobs = list(
        (
            await db_session.scalars(
                select(DurableJob).where(
                    DurableJob.tenant_id == test_tenant.id,
                    DurableJob.kind == "zoom_phone_reconcile",
                )
            )
        ).all()
    )
    assert len(jobs) == 1
    assert jobs[0].status == "pending"
    assert jobs[0].idempotency_key.startswith("hour:")


@pytest.mark.asyncio
async def test_reconciliation_skips_app_grant_account_mismatch(
    db_session, test_tenant, test_user
):
    app, _credential = await _configure_zoom(db_session, test_tenant, test_user)
    app.zoom_account_id = "different-account-456"
    await db_session.commit()

    await enqueue_zoom_phone_reconciliation_jobs()
    await db_session.rollback()
    jobs = list(
        (
            await db_session.scalars(
                select(DurableJob).where(
                    DurableJob.tenant_id == test_tenant.id,
                    DurableJob.kind == "zoom_phone_reconcile",
                )
            )
        ).all()
    )
    assert jobs == []


@pytest.mark.asyncio
async def test_changing_tenant_zoom_client_invalidates_existing_grant(
    client, db_session, test_tenant, test_user
):
    _, credential = await _configure_zoom(db_session, test_tenant, test_user)
    response = await client.put(
        "/api/integrations/zoom-phone/app-credentials",
        json={
            "client_id": "replacement-client",
            "client_secret": "replacement-secret",
            "webhook_secret_token": "replacement-webhook-secret",
        },
    )
    assert response.status_code == 200, response.text
    await db_session.refresh(credential)
    assert credential.is_active is False
    assert credential.health == "reauthorization_required"


def test_production_gate_runs_backend_zoom_api_probe():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    production_check = (root / "scripts" / "production_check.sh").read_text(
        encoding="utf-8"
    )
    probe = (root / "backend" / "scripts" / "check_zoom_phone.py").read_text(
        encoding="utf-8"
    )
    assert "python -m scripts.check_zoom_phone" in production_check
    assert "probe_zoom_phone_connection" in probe
    assert "app.zoom_account_id.strip()" in probe
    assert "grant.service_account_email.strip()" in probe
    assert "secrets.compare_digest" in probe
    assert "print(tenant_id" not in probe
    assert 'print(f"{tenant_id}' not in probe
    assert "Zoom Phone API probe passed for the required tenant." in probe
    assert '--tenant-id "$ZOOM_REQUIRED_TENANT_ID"' in production_check


def test_zoom_shell_gates_are_strict_by_default_and_bootstrap_is_explicit():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    production_path = root / "scripts" / "production_check.sh"
    deploy_path = root / "scripts" / "deploy_prod.sh"
    production = production_path.read_text(encoding="utf-8")
    deploy = deploy_path.read_text(encoding="utf-8")
    bash_bin = os.environ.get("BASH", "bash")

    assert 'ZOOM_REQUIRED="${ZOOM_REQUIRED:-true}"' in production
    assert 'if [[ "$ZOOM_REQUIRED" == true ]]; then' in production
    assert "NOT GO-LIVE" in production
    assert production.count('if [[ "$ZOOM_REQUIRED" == true ]]; then') >= 3
    assert 'printf \'%s\' "$state" > "$STATE_FILE"' in production
    assert 'BOOTSTRAP_MODE="${BOOTSTRAP_MODE:-false}"' in deploy
    assert 'if [[ "$BOOTSTRAP_MODE" == true ]]; then' in deploy
    assert 'ZOOM_REQUIRED="$zoom_required" bash scripts/production_check.sh' in deploy
    assert deploy.index("prod_data_guard.sh post") < deploy.index(
        "bash scripts/production_check.sh"
    )
    assert "$HOME/.local/state" in production
    assert "$ROOT_DIR/.monitor-state" not in production

    invalid_zoom = subprocess.run(
        [bash_bin, str(production_path)],
        env={**os.environ, "ZOOM_REQUIRED": "sometimes"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert invalid_zoom.returncode == 2
    assert "ZOOM_REQUIRED must be true or false" in invalid_zoom.stderr

    invalid_bootstrap = subprocess.run(
        [bash_bin, str(deploy_path)],
        env={**os.environ, "BOOTSTRAP_MODE": "sometimes"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert invalid_bootstrap.returncode == 2
    assert "BOOTSTRAP_MODE must be true or false" in invalid_bootstrap.stderr
