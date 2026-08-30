"""End-to-end security and scale contracts for the file-share relay."""

import asyncio
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models.smb_access_log import SmbAccessLog
from app.models.smb_file_index import SmbFileIndex
from app.models.tenant import Tenant


@pytest_asyncio.fixture(autouse=True)
async def _clear_agent_registration_limit(test_redis):
    keys = await test_redis.keys("rate:auth:/api/v1/smb/agents/register:*")
    if keys:
        await test_redis.delete(*keys)


async def _register_agent(client):
    code = (await client.post("/api/v1/smb/pairing-code")).json()["pairing_code"]
    response = await client.post(
        "/api/v1/smb/agents/register",
        json={
            "pairing_code": code,
            "agent_name": "Pipeline Agent",
            "agent_version": "0.13.0",
            "hostname": "fs01",
            "os_info": "Windows Server 2022",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["agent_id"], response.json()["api_key"]


async def _create_share(client, agent_id, share_path="\\\\FS01\\Legal"):
    response = await client.post(
        "/api/v1/smb/shares",
        params={"agent_id": agent_id},
        json={
            "share_path": share_path,
            "display_name": share_path.rsplit("\\", 1)[-1],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def _file(path, snippet):
    return {
        "path": path,
        "filename": path.rsplit("\\", 1)[-1],
        "ext": ".txt",
        "mime_type": "text/plain",
        "snippet": snippet,
        "size_bytes": len(snippet),
    }


async def _sync(client, agent_id, api_key, share_id, files, deletions=None):
    return await client.post(
        f"/api/v1/smb/agents/{agent_id}/sync",
        params={"share_id": share_id},
        headers={"X-Agent-API-Key": api_key},
        json={"files": files, "deletions": deletions or []},
    )


@pytest.mark.asyncio
async def test_index_cap_blocks_only_new_rows(
    client, db_session, test_tenant, monkeypatch
):
    """An index at its cap must still accept changes to existing files."""
    monkeypatch.setattr("app.services.smb.SMB_MAX_FILE_INDEX_PER_SHARE", 1)
    agent_id, api_key = await _register_agent(client)
    share_id = await _create_share(client, agent_id)
    first_path = "\\\\FS01\\Legal\\first.txt"

    first = await _sync(
        client, agent_id, api_key, share_id, [_file(first_path, "version one")]
    )
    changed = await _sync(
        client, agent_id, api_key, share_id, [_file(first_path, "version two")]
    )
    overflow = await _sync(
        client,
        agent_id,
        api_key,
        share_id,
        [_file("\\\\FS01\\Legal\\second.txt", "new row")],
    )

    assert first.status_code == 200 and first.json()["synced"] == 1
    assert changed.status_code == 200 and changed.json() == {
        "synced": 1,
        "deleted": 0,
        "errors": [],
    }
    assert overflow.status_code == 200
    assert overflow.json()["synced"] == 0
    assert overflow.json()["errors"][0]["error"] == "Share file index cap reached"
    revision = await db_session.scalar(
        select(Tenant.rag_corpus_revision).where(Tenant.id == test_tenant.id)
    )
    assert revision == 2  # initial insert + accepted update; rejected row is ignored


@pytest.mark.asyncio
async def test_content_result_is_bound_to_tenant_file_share_and_pending_task(
    client, db_session
):
    agent_id, api_key = await _register_agent(client)
    share_id = await _create_share(client, agent_id)
    first_path = "\\\\FS01\\Legal\\first.txt"
    second_path = "\\\\FS01\\Legal\\second.txt"
    synced = await _sync(
        client,
        agent_id,
        api_key,
        share_id,
        [_file(first_path, "first"), _file(second_path, "second")],
    )
    assert synced.status_code == 200, synced.text

    rows = (
        await db_session.execute(
            select(SmbFileIndex).where(SmbFileIndex.share_id == uuid.UUID(share_id))
        )
    ).scalars()
    file_ids = {row.path: row.id for row in rows}

    queued = await client.post(
        f"/api/v1/smb/files/{file_ids[first_path]}/fetch-content"
    )
    assert queued.status_code == 200, queued.text
    task_id = queued.json()["task_id"]

    tasks = await client.get(
        f"/api/v1/smb/agents/{agent_id}/tasks",
        headers={"X-Agent-API-Key": api_key},
    )
    task = next(item for item in tasks.json() if item["task_id"] == task_id)
    assert task["share_id"] == share_id
    assert task["file_path"] == first_path

    injected = await client.post(
        f"/api/v1/smb/agents/{agent_id}/tasks/not-pending/result",
        headers={"X-Agent-API-Key": api_key},
        json={"task_id": "not-pending", "content": "must not be stored"},
    )
    assert injected.status_code == 404

    reported = await client.post(
        f"/api/v1/smb/agents/{agent_id}/tasks/{task_id}/result",
        headers={"X-Agent-API-Key": api_key},
        json={"task_id": task_id, "content": "full first document"},
    )
    assert reported.status_code == 200, reported.text
    retried = await client.post(
        f"/api/v1/smb/agents/{agent_id}/tasks/{task_id}/result",
        headers={"X-Agent-API-Key": api_key},
        json={"task_id": task_id, "content": "full first document"},
    )
    assert retried.status_code == 200, retried.text

    ready = await client.get(
        f"/api/v1/smb/files/{file_ids[first_path]}/content-status",
        params={"task_id": task_id},
    )
    wrong_file = await client.get(
        f"/api/v1/smb/files/{file_ids[second_path]}/content-status",
        params={"task_id": task_id},
    )
    assert ready.json() == {
        "status": "ready",
        "content": "full first document",
        "truncated": False,
    }
    assert wrong_file.status_code == 404

    log = (await db_session.execute(select(SmbAccessLog))).scalar_one()
    assert log.bytes_sent == len("full first document".encode("utf-8"))


@pytest.mark.asyncio
async def test_content_read_failure_is_not_reported_as_an_empty_file(
    client, db_session
):
    agent_id, api_key = await _register_agent(client)
    share_id = await _create_share(client, agent_id)
    path = "\\\\FS01\\Legal\\missing.txt"
    await _sync(client, agent_id, api_key, share_id, [_file(path, "metadata")])
    file_id = (
        await db_session.execute(
            select(SmbFileIndex.id).where(SmbFileIndex.path == path)
        )
    ).scalar_one()

    queued = await client.post(f"/api/v1/smb/files/{file_id}/fetch-content")
    task_id = queued.json()["task_id"]
    await client.post(
        f"/api/v1/smb/agents/{agent_id}/tasks/{task_id}/result",
        headers={"X-Agent-API-Key": api_key},
        json={"task_id": task_id, "error": "No such file on the share"},
    )

    status = await client.get(
        f"/api/v1/smb/files/{file_id}/content-status",
        params={"task_id": task_id},
    )
    assert status.status_code == 200
    assert status.json() == {
        "status": "failed",
        "error": "No such file on the share",
    }


@pytest.mark.asyncio
async def test_matter_folder_scope_pairs_each_folder_with_its_share(client, db_session):
    agent_id, api_key = await _register_agent(client)
    share_a = await _create_share(client, agent_id, "\\\\FS01\\CasesA")
    share_b = await _create_share(client, agent_id, "\\\\FS01\\CasesB")

    a_inside = "\\\\FS01\\CasesA\\Client-1\\inside-a.txt"
    a_sibling = "\\\\FS01\\CasesA\\Client-10\\sibling.txt"
    b_wrong_folder = "\\\\FS01\\CasesB\\Client-1\\wrong-b.txt"
    b_inside = "\\\\FS01\\CasesB\\Client-2\\inside-b.txt"
    await _sync(
        client,
        agent_id,
        api_key,
        share_a,
        [_file(a_inside, "needle"), _file(a_sibling, "needle")],
    )
    await _sync(
        client,
        agent_id,
        api_key,
        share_b,
        [_file(b_wrong_folder, "needle"), _file(b_inside, "needle")],
    )

    matter = await client.post(
        "/api/matters",
        json={"matter_name": "Scoped file matter", "practice_area": "litigation"},
    )
    assert matter.status_code == 201, matter.text
    matter_id = matter.json()["id"]

    for share_id, folder in ((share_a, "Client-1"), (share_b, "Client-2")):
        bound = await client.post(
            f"/api/v1/smb/matters/{matter_id}/smb-shares",
            json={"share_id": share_id, "folder_path": folder},
        )
        assert bound.status_code == 200, bound.text

    result = await client.get(
        "/api/v1/smb/files/search",
        params={"q": "needle", "matter_id": matter_id},
    )
    assert result.status_code == 200, result.text
    assert {item["path"] for item in result.json()} == {a_inside, b_inside}

    indexed_rows = (
        await db_session.execute(
            select(SmbFileIndex).where(
                SmbFileIndex.path.in_([a_inside, a_sibling, b_wrong_folder])
            )
        )
    ).scalars()
    file_ids = {row.path: str(row.id) for row in indexed_rows}
    resolved = await client.get(
        f"/api/v1/smb/files/{file_ids[a_inside]}/detail",
        params={"matter_id": matter_id},
    )
    sibling = await client.get(
        f"/api/v1/smb/files/{file_ids[a_sibling]}/detail",
        params={"matter_id": matter_id},
    )
    wrong_share_folder = await client.get(
        f"/api/v1/smb/files/{file_ids[b_wrong_folder]}/detail",
        params={"matter_id": matter_id},
    )
    assert resolved.status_code == 200
    assert resolved.json()["path"] == a_inside
    assert sibling.status_code == 404
    assert wrong_share_folder.status_code == 404

    traversal = await client.post(
        f"/api/v1/smb/matters/{matter_id}/smb-shares",
        json={"share_id": share_a, "folder_path": "../other-matter"},
    )
    assert traversal.status_code == 400
    assert "within the assigned share" in traversal.json()["detail"]


@pytest.mark.asyncio
async def test_local_search_fanout_validates_agent_hits_against_matter_scope(client):
    agent_id, api_key = await _register_agent(client)
    share_id = await _create_share(client, agent_id, "\\\\FS01\\Cases")
    inside = "\\\\FS01\\Cases\\Client-1\\inside.txt"
    outside = "\\\\FS01\\Cases\\Client-10\\outside.txt"
    synced = await _sync(
        client,
        agent_id,
        api_key,
        share_id,
        [_file(inside, "metadata only"), _file(outside, "metadata only")],
    )
    assert synced.status_code == 200, synced.text

    matter = await client.post(
        "/api/matters",
        json={"matter_name": "Local search matter", "practice_area": "litigation"},
    )
    matter_id = matter.json()["id"]
    bound = await client.post(
        f"/api/v1/smb/matters/{matter_id}/smb-shares",
        json={"share_id": share_id, "folder_path": "Client-1"},
    )
    assert bound.status_code == 200, bound.text

    search_future = asyncio.create_task(
        client.post(
            "/api/v1/smb/local-search",
            json={
                "query": "summary judgment",
                "matter_id": matter_id,
                "file_extensions": ["txt"],
                "limit": 10,
                "correlation_id": "poc-run-123",
            },
        )
    )
    tasks = await client.get(
        f"/api/v1/smb/agents/{agent_id}/tasks",
        params={"wait_seconds": 2},
        headers={"X-Agent-API-Key": api_key},
    )
    assert tasks.status_code == 200, tasks.text
    search_task = next(item for item in tasks.json() if item["kind"] == "local_search")
    assert search_task["query"] == "summary judgment"
    assert search_task["correlation_id"] == "poc-run-123"
    assert search_task["file_extensions"] == [".txt"]
    assert search_task["scopes"] == [{"share_id": share_id, "folder_path": "Client-1"}]

    reported = await client.post(
        f"/api/v1/smb/agents/{agent_id}/tasks/{search_task['task_id']}/result",
        headers={"X-Agent-API-Key": api_key},
        json={
            "task_id": search_task["task_id"],
            "ok": True,
            "detail": {
                "schema_version": 1,
                "correlation_id": "poc-run-123",
                "index_state": "ready",
                "indexed_files": 2,
                "pending_files": 0,
                "duration_ms": 17,
                "hits": [
                    {
                        "share_id": share_id,
                        "relative_path": "Client-1\\inside.txt",
                        "filename": "agent-controlled-name.txt",
                        "ext": ".txt",
                        "snippet": "Relevant page-level passage.",
                        "page_number": 3,
                        "score": 9.5,
                    },
                    {
                        "share_id": share_id,
                        "relative_path": "Client-10\\outside.txt",
                        "filename": "outside.txt",
                        "snippet": "Must not escape the binding.",
                        "score": 99,
                    },
                    {
                        "share_id": share_id,
                        "relative_path": "Client-1\\not-indexed.txt",
                        "filename": "not-indexed.txt",
                        "snippet": "Must exist in the SaaS metadata index.",
                        "score": 100,
                    },
                ],
            },
        },
    )
    assert reported.status_code == 200, reported.text
    response = await search_future

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["correlation_id"] == "poc-run-123"
    assert payload["partial"] is False
    assert payload["degraded"] is False
    assert len(payload["hits"]) == 1
    assert payload["hits"][0]["path"] == inside
    assert payload["hits"][0]["filename"] == "inside.txt"
    assert payload["hits"][0]["snippet"] == "Relevant page-level passage."
    assert payload["hits"][0]["page_number"] == 3
    assert payload["agent_statuses"][0]["index_state"] == "ready"
    assert "summary judgment" not in response.text
