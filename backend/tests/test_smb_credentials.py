"""File share credential vault: storage, delivery, and connection tests.

Covers the security contract that matters here — an admin can configure how a
share authenticates without ever reading a secret back, while the paired agent
(and only that agent) receives the decrypted credential it needs to mount the
share.
"""

import json
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models.smb_credential import SmbCredential
from app.models.smb_share import SmbShare
from app.services.token_vault import decrypt_token


@pytest_asyncio.fixture(autouse=True)
async def _clear_registration_rate_limit(test_redis):
    """Agent registration is IP rate-limited; these tests pair several agents."""
    keys = await test_redis.keys("rate:auth:/api/v1/smb/agents/register:*")
    if keys:
        await test_redis.delete(*keys)
    yield


async def _register_agent(client, name="FS01 Agent"):
    """Pair an agent the way the installer does and return (id, api key)."""
    code_response = await client.post("/api/v1/smb/pairing-code")
    assert code_response.status_code == 200
    code = code_response.json()["pairing_code"]

    register = await client.post(
        "/api/v1/smb/agents/register",
        json={
            "pairing_code": code,
            "agent_name": name,
            "agent_version": "0.13.0",
            "hostname": "fs01",
            "os_info": "Windows Server 2022",
        },
    )
    assert register.status_code == 200, register.text
    body = register.json()
    return body["agent_id"], body["api_key"]


async def _create_share(client, agent_id, **overrides):
    payload = {
        "share_path": "\\\\FS01\\Legal",
        "display_name": "Legal Documents",
        "credential": {
            "name": "svc-lawhand",
            "auth_method": "ntlm",
            "domain": "CORP",
            "username": "svc-lawhand",
            "password": "correct horse battery staple",
        },
    }
    payload.update(overrides)
    response = await client.post(
        "/api/v1/smb/shares", params={"agent_id": agent_id}, json=payload
    )
    return response


@pytest.mark.asyncio
async def test_credential_is_stored_encrypted_and_never_returned(client, db_session):
    response = await client.post(
        "/api/v1/smb/credentials",
        json={
            "name": "svc-lawhand (CORP)",
            "auth_method": "ntlm",
            "domain": "CORP",
            "username": "svc-lawhand",
            "password": "s3cret-share-password",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["has_password"] is True
    assert "password" not in body
    # The secret must not appear anywhere in the admin-facing payload.
    assert "s3cret-share-password" not in response.text

    stored = (
        await db_session.execute(
            select(SmbCredential).where(SmbCredential.id == uuid.UUID(body["id"]))
        )
    ).scalar_one()
    assert stored.encrypted_password != "s3cret-share-password"
    assert decrypt_token(stored.encrypted_password) == "s3cret-share-password"


@pytest.mark.asyncio
async def test_listing_credentials_hides_secrets_and_counts_shares(client):
    agent_id, _ = await _register_agent(client)
    created = await _create_share(client, agent_id)
    assert created.status_code == 200, created.text

    response = await client.get("/api/v1/smb/credentials")

    assert response.status_code == 200
    assert "correct horse battery staple" not in response.text
    entry = next(c for c in response.json() if c["name"] == "svc-lawhand")
    assert entry["share_count"] == 1
    assert entry["has_password"] is True


@pytest.mark.asyncio
async def test_ntlm_credential_requires_username_and_password(client):
    response = await client.post(
        "/api/v1/smb/credentials",
        json={"name": "incomplete", "auth_method": "ntlm", "username": "svc"},
    )

    assert response.status_code == 400
    assert "password" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_kerberos_credential_needs_no_secret(client):
    response = await client.post(
        "/api/v1/smb/credentials",
        json={"name": "kerberos-host", "auth_method": "kerberos"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["has_password"] is False


@pytest.mark.asyncio
async def test_share_creation_attaches_an_inline_credential(client):
    agent_id, _ = await _register_agent(client)

    response = await _create_share(client, agent_id)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["credential_id"]
    assert body["credential_name"] == "svc-lawhand"
    assert "correct horse battery staple" not in response.text


@pytest.mark.asyncio
async def test_agent_receives_the_decrypted_credential_for_its_shares(client):
    agent_id, api_key = await _register_agent(client)
    assert (await _create_share(client, agent_id)).status_code == 200

    response = await client.get(
        f"/api/v1/smb/agents/{agent_id}/shares",
        headers={"X-Agent-API-Key": api_key},
    )

    assert response.status_code == 200, response.text
    share = response.json()[0]
    assert share["server"] == "FS01"
    assert share["share"] == "Legal"
    assert share["credential"]["username"] == "svc-lawhand"
    assert share["credential"]["domain"] == "CORP"
    assert share["credential"]["password"] == "correct horse battery staple"


@pytest.mark.asyncio
async def test_agent_api_key_is_required_for_share_credentials(client):
    agent_id, _ = await _register_agent(client)
    assert (await _create_share(client, agent_id)).status_code == 200

    response = await client.get(f"/api/v1/smb/agents/{agent_id}/shares")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_credential_pinned_to_another_agent_is_not_delivered(client):
    agent_a, key_a = await _register_agent(client, name="Agent A")
    agent_b, _ = await _register_agent(client, name="Agent B")

    pinned = await client.post(
        "/api/v1/smb/credentials",
        json={
            "name": "pinned-to-b",
            "auth_method": "ntlm",
            "username": "svc-b",
            "password": "b-secret",
            "agent_id": agent_b,
        },
    )
    assert pinned.status_code == 200

    # Attaching another agent's pinned credential is rejected outright.
    rejected = await client.post(
        "/api/v1/smb/shares",
        params={"agent_id": agent_a},
        json={
            "share_path": "\\\\FS02\\Ops",
            "display_name": "Ops",
            "credential_id": pinned.json()["id"],
        },
    )

    assert rejected.status_code == 400
    assert "pinned" in rejected.json()["detail"].lower()


@pytest.mark.asyncio
async def test_updating_a_credential_without_a_password_keeps_the_secret(client):
    agent_id, api_key = await _register_agent(client)
    assert (await _create_share(client, agent_id)).status_code == 200
    credential_id = (await client.get("/api/v1/smb/credentials")).json()[0]["id"]

    updated = await client.patch(
        f"/api/v1/smb/credentials/{credential_id}",
        json={"name": "svc-lawhand renamed", "domain": "CORP2"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["has_password"] is True

    delivered = await client.get(
        f"/api/v1/smb/agents/{agent_id}/shares",
        headers={"X-Agent-API-Key": api_key},
    )
    assert delivered.json()[0]["credential"]["password"] == "correct horse battery staple"
    assert delivered.json()[0]["credential"]["domain"] == "CORP2"


@pytest.mark.asyncio
async def test_deleting_a_credential_detaches_its_shares(client, db_session):
    agent_id, _ = await _register_agent(client)
    share_id = (await _create_share(client, agent_id)).json()["id"]
    credential_id = (await client.get("/api/v1/smb/credentials")).json()[0]["id"]

    response = await client.delete(f"/api/v1/smb/credentials/{credential_id}")

    assert response.status_code == 200
    assert response.json()["detached_shares"] == 1

    await db_session.commit()
    share = (
        await db_session.execute(
            select(SmbShare).where(SmbShare.id == uuid.UUID(share_id))
        )
    ).scalar_one()
    assert share.credential_id is None


@pytest.mark.asyncio
async def test_connection_test_round_trip_updates_the_share(client, test_redis):
    agent_id, api_key = await _register_agent(client)
    share_id = (await _create_share(client, agent_id)).json()["id"]

    queued = await client.post(f"/api/v1/smb/shares/{share_id}/test-connection")
    assert queued.status_code == 200, queued.text
    task_id = queued.json()["task_id"]
    assert queued.json()["kind"] == "verify_share"

    # The agent picks the task up on its next poll.
    tasks = await client.get(
        f"/api/v1/smb/agents/{agent_id}/tasks",
        headers={"X-Agent-API-Key": api_key},
    )
    assert tasks.status_code == 200
    task = next(t for t in tasks.json() if t["task_id"] == task_id)
    assert task["kind"] == "verify_share"
    assert task["share_id"] == share_id

    # Before the result arrives the admin poll reports pending.
    pending = await client.get(f"/api/v1/smb/shares/{share_id}/task/{task_id}")
    assert pending.json()["status"] == "pending"

    reported = await client.post(
        f"/api/v1/smb/agents/{agent_id}/tasks/{task_id}/result",
        headers={"X-Agent-API-Key": api_key},
        json={
            "task_id": task_id,
            "ok": True,
            "detail": {"identity": "CORP\\svc-lawhand (ntlm)", "entries_sampled": 12},
        },
    )
    assert reported.status_code == 200, reported.text

    result = await client.get(f"/api/v1/smb/shares/{share_id}/task/{task_id}")
    assert result.json()["status"] == "ok"
    assert result.json()["detail"]["entries_sampled"] == 12

    shares = await client.get("/api/v1/smb/shares")
    share = next(s for s in shares.json() if s["id"] == share_id)
    assert share["last_verify_status"] == "ok"

    credential = (await client.get("/api/v1/smb/credentials")).json()[0]
    assert credential["last_verify_status"] == "ok"


@pytest.mark.asyncio
async def test_failed_connection_test_surfaces_the_agent_error(client):
    agent_id, api_key = await _register_agent(client)
    share_id = (await _create_share(client, agent_id)).json()["id"]

    task_id = (
        await client.post(f"/api/v1/smb/shares/{share_id}/test-connection")
    ).json()["task_id"]

    await client.post(
        f"/api/v1/smb/agents/{agent_id}/tasks/{task_id}/result",
        headers={"X-Agent-API-Key": api_key},
        json={"task_id": task_id, "ok": False, "error": "SMBAuthenticationError: logon failure"},
    )

    shares = await client.get("/api/v1/smb/shares")
    share = next(s for s in shares.json() if s["id"] == share_id)
    assert share["last_verify_status"] == "failed"
    assert "logon failure" in share["last_verify_error"]


@pytest.mark.asyncio
async def test_scan_now_is_queued_for_the_agent(client, test_redis):
    agent_id, api_key = await _register_agent(client)
    share_id = (await _create_share(client, agent_id)).json()["id"]

    queued = await client.post(f"/api/v1/smb/shares/{share_id}/scan")

    assert queued.status_code == 200, queued.text
    assert queued.json()["kind"] == "scan_now"
    raw = await test_redis.get(
        f"smb_task_pending:{agent_id}:{queued.json()['task_id']}"
    )
    payload = json.loads(raw if isinstance(raw, str) else raw.decode())
    assert payload["kind"] == "scan_now"
    assert payload["share_path"] == "\\\\FS01\\Legal"

    tasks = await client.get(
        f"/api/v1/smb/agents/{agent_id}/tasks",
        headers={"X-Agent-API-Key": api_key},
    )
    assert any(t["task_id"] == queued.json()["task_id"] for t in tasks.json())


@pytest.mark.asyncio
async def test_agent_scan_status_report_is_visible_to_admins(client):
    agent_id, api_key = await _register_agent(client)
    share_id = (await _create_share(client, agent_id)).json()["id"]

    reported = await client.post(
        f"/api/v1/smb/agents/{agent_id}/shares/{share_id}/scan-status",
        headers={"X-Agent-API-Key": api_key},
        json={"status": "success", "file_count": 4210},
    )
    assert reported.status_code == 200, reported.text

    shares = await client.get("/api/v1/smb/shares")
    share = next(s for s in shares.json() if s["id"] == share_id)
    assert share["last_scan_status"] == "success"
    assert share["last_scan_file_count"] == 4210
    assert share["last_scan_at"] is not None


@pytest.mark.asyncio
async def test_agent_cannot_report_scan_status_for_another_agents_share(client):
    agent_a, _ = await _register_agent(client, name="Agent A")
    agent_b, key_b = await _register_agent(client, name="Agent B")
    share_id = (await _create_share(client, agent_a)).json()["id"]

    response = await client.post(
        f"/api/v1/smb/agents/{agent_b}/shares/{share_id}/scan-status",
        headers={"X-Agent-API-Key": key_b},
        json={"status": "success", "file_count": 1},
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_share_update_can_detach_a_credential(client):
    agent_id, api_key = await _register_agent(client)
    share_id = (await _create_share(client, agent_id)).json()["id"]

    updated = await client.patch(
        f"/api/v1/smb/shares/{share_id}",
        json={"credential_id": ""},
    )

    assert updated.status_code == 200, updated.text
    assert updated.json()["credential_id"] is None

    delivered = await client.get(
        f"/api/v1/smb/agents/{agent_id}/shares",
        headers={"X-Agent-API-Key": api_key},
    )
    assert delivered.json()[0]["credential"] is None


@pytest.mark.asyncio
async def test_share_scan_scope_is_normalized(client):
    agent_id, api_key = await _register_agent(client)

    created = await _create_share(
        client,
        agent_id,
        file_extensions=["PDF", ".Docx", " txt "],
        exclude_patterns=["~$*"],
        max_depth=4,
    )

    assert created.status_code == 200, created.text
    assert created.json()["file_extensions"] == [".pdf", ".docx", ".txt"]

    delivered = await client.get(
        f"/api/v1/smb/agents/{agent_id}/shares",
        headers={"X-Agent-API-Key": api_key},
    )
    share = delivered.json()[0]
    assert share["exclude_patterns"] == ["~$*"]
    assert share["max_depth"] == 4


@pytest.mark.asyncio
async def test_deactivated_credential_is_not_delivered_to_the_agent(client):
    agent_id, api_key = await _register_agent(client)
    assert (await _create_share(client, agent_id)).status_code == 200
    credential_id = (await client.get("/api/v1/smb/credentials")).json()[0]["id"]

    disabled = await client.patch(
        f"/api/v1/smb/credentials/{credential_id}", json={"is_active": False}
    )
    assert disabled.status_code == 200
    assert disabled.json()["is_active"] is False

    delivered = await client.get(
        f"/api/v1/smb/agents/{agent_id}/shares",
        headers={"X-Agent-API-Key": api_key},
    )
    assert delivered.json()[0]["credential"] is None


@pytest.mark.asyncio
async def test_switching_to_kerberos_drops_the_stored_password(client):
    created = await client.post(
        "/api/v1/smb/credentials",
        json={
            "name": "was-ntlm",
            "auth_method": "ntlm",
            "username": "svc",
            "password": "old-secret",
        },
    )
    credential_id = created.json()["id"]

    switched = await client.patch(
        f"/api/v1/smb/credentials/{credential_id}", json={"auth_method": "kerberos"}
    )

    assert switched.status_code == 200, switched.text
    assert switched.json()["has_password"] is False


@pytest.mark.asyncio
async def test_duplicate_credential_names_are_rejected(client):
    body = {
        "name": "duplicate",
        "auth_method": "ntlm",
        "username": "svc",
        "password": "pw",
    }
    assert (await client.post("/api/v1/smb/credentials", json=body)).status_code == 200

    second = await client.post("/api/v1/smb/credentials", json=body)

    assert second.status_code == 400
    assert "already exists" in second.json()["detail"]


@pytest.mark.asyncio
async def test_unknown_credential_returns_404(client):
    missing = str(uuid.uuid4())

    patched = await client.patch(
        f"/api/v1/smb/credentials/{missing}", json={"name": "nope"}
    )
    deleted = await client.delete(f"/api/v1/smb/credentials/{missing}")

    assert patched.status_code == 404
    assert deleted.status_code == 404


@pytest.mark.asyncio
async def test_task_actions_require_an_active_agent(client):
    agent_id, _ = await _register_agent(client)
    share_id = (await _create_share(client, agent_id)).json()["id"]
    paused = await client.patch(
        f"/api/v1/smb/agents/{agent_id}", json={"status": "paused"}
    )
    assert paused.status_code == 200

    response = await client.post(f"/api/v1/smb/shares/{share_id}/test-connection")

    assert response.status_code == 409
    assert "paused" in response.json()["detail"]


@pytest.mark.asyncio
async def test_task_actions_404_for_an_unknown_share(client):
    response = await client.post(
        f"/api/v1/smb/shares/{uuid.uuid4()}/test-connection"
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_pairing_code_fits_the_column_and_is_typeable(client):
    response = await client.post("/api/v1/smb/pairing-code")

    assert response.status_code == 200, response.text
    code = response.json()["pairing_code"]
    # smb_agents.pairing_code is varchar(20); a longer code fails at insert.
    assert len(code) <= 20
    assert set(code) <= set("23456789ABCDEFGHJKMNPQRSTVWXYZ-")
