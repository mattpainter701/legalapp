from __future__ import annotations

import asyncio
import sqlite3

import pytest

from clarity_agent import search_control as control_module
from clarity_agent.search_control import IndexJob, ManifestEntry, SqliteControlState


@pytest.mark.asyncio
async def test_sqlite_control_state_contains_manifest_and_jobs_but_no_full_text(
    tmp_path,
):
    path = tmp_path / "control.db"
    state = SqliteControlState(str(path))
    await state.init()
    await state.upsert_manifest(
        [
            ManifestEntry(
                document_id="doc-1",
                share_id="share-1",
                relative_path="brief.pdf",
                content_hash="abc",
                document_version="v3",
                modified_at="2026-08-31T00:00:00Z",
                size_bytes=42,
                index_schema_version=1,
            )
        ]
    )
    await state.enqueue([IndexJob(document_id="doc-1", operation="upsert")])
    claimed = await state.claim()
    assert claimed and claimed.document_id == "doc-1" and claimed.attempts == 1
    assert claimed.lease_token
    await state.complete("doc-1", claimed.lease_token)
    await state.close()

    db = sqlite3.connect(path)
    schema = " ".join(
        row[0] or ""
        for row in db.execute(
            "SELECT sql FROM sqlite_master WHERE type IN ('table','index')"
        )
    ).lower()
    # A content hash is manifest metadata; extracted content is forbidden.
    assert "content text" not in schema
    assert "snippet" not in schema
    assert "body" not in schema
    assert not any(
        "fts" in row[0].lower() for row in db.execute("SELECT name FROM sqlite_master")
    )
    db.close()


@pytest.mark.asyncio
async def test_control_state_persists_only_stable_error_category(tmp_path):
    path = tmp_path / "control.db"
    state = SqliteControlState(str(path))
    await state.init()
    await state.enqueue([IndexJob(document_id="doc-1", operation="upsert")])
    claimed = await state.claim()
    assert claimed and claimed.lease_token
    await state.fail(
        "doc-1", claimed.lease_token, r"parser leaked C:\Client\secret.txt", None
    )
    await state.close()
    db = sqlite3.connect(path)
    assert (
        db.execute("SELECT error_code FROM search_jobs").fetchone()[0]
        == "internal_error"
    )
    db.close()


@pytest.mark.asyncio
async def test_control_state_rejects_alphanumeric_non_allowlisted_error(tmp_path):
    path = tmp_path / "control.db"
    state = SqliteControlState(str(path))
    await state.init()
    await state.enqueue([IndexJob(document_id="doc-1", operation="upsert")])
    claimed = await state.claim()
    assert claimed and claimed.lease_token
    await state.fail("doc-1", claimed.lease_token, "ConfidentialAcquisition", None)
    await state.close()
    db = sqlite3.connect(path)
    assert (
        db.execute("SELECT error_code FROM search_jobs").fetchone()[0]
        == "internal_error"
    )
    db.close()


def test_control_state_requires_absolute_local_path():
    with pytest.raises(ValueError, match="absolute"):
        SqliteControlState("relative/control.db")
    with pytest.raises(ValueError, match="local storage"):
        SqliteControlState(r"\\server\share\control.db")


@pytest.mark.asyncio
async def test_control_state_requires_directory_and_database_hardening(
    tmp_path, monkeypatch
):
    calls = []

    def restrict(path, *, required=False):
        calls.append((path, required))

    monkeypatch.setattr(control_module, "_restrict", restrict)
    path = tmp_path / "private" / "control.db"
    state = SqliteControlState(str(path))
    await state.init()
    await state.close()
    assert calls == [(path.parent, True), (path, True)]


@pytest.mark.asyncio
async def test_control_state_closes_database_when_hardening_fails(
    tmp_path, monkeypatch
):
    path = tmp_path / "private" / "control.db"

    def restrict(target, *, required=False):
        if target == path:
            raise PermissionError("denied")

    monkeypatch.setattr(control_module, "_restrict", restrict)
    state = SqliteControlState(str(path))
    with pytest.raises(PermissionError, match="denied"):
        await state.init()
    assert state._db is None


@pytest.mark.asyncio
async def test_reenqueue_invalidates_old_worker_lease(tmp_path):
    state = SqliteControlState(str(tmp_path / "control.db"))
    await state.init()
    job = IndexJob(document_id="doc-1", operation="upsert")
    await state.enqueue([job])
    old = await state.claim()
    assert old and old.lease_token and old.generation == 1
    await state.enqueue([job])
    with pytest.raises(RuntimeError, match="stale"):
        await state.complete("doc-1", old.lease_token)
    current = await state.claim()
    assert (
        current
        and current.lease_token
        and current.lease_token != old.lease_token
        and current.generation == 2
    )
    await state.complete("doc-1", current.lease_token)
    await state.close()


@pytest.mark.asyncio
async def test_expired_lease_cannot_complete_reclaimed_job(tmp_path):
    state = SqliteControlState(str(tmp_path / "control.db"))
    await state.init()
    await state.enqueue([IndexJob(document_id="doc-1", operation="upsert")])
    expired = await state.claim()
    assert expired and expired.lease_token
    await state._require_db().execute(
        "UPDATE search_jobs SET lease_until=0 WHERE document_id='doc-1'"
    )
    await state._require_db().commit()
    current = await state.claim()
    assert (
        current
        and current.lease_token
        and current.lease_token != expired.lease_token
        and current.generation == expired.generation + 1
    )
    with pytest.raises(RuntimeError, match="stale"):
        await state.complete("doc-1", expired.lease_token)
    await state.complete("doc-1", current.lease_token)
    await state.close()


@pytest.mark.asyncio
async def test_concurrent_claims_and_enqueue_serialize_one_connection(tmp_path):
    state = SqliteControlState(str(tmp_path / "control.db"))
    await state.init()
    await state.enqueue(
        [
            IndexJob(document_id=f"doc-{number}", operation="upsert")
            for number in range(4)
        ]
    )
    claims = await asyncio.gather(*(state.claim() for _ in range(6)))
    claimed = [job for job in claims if job is not None]
    assert len(claimed) == 4
    assert len({job.document_id for job in claimed}) == 4
    assert all(job.generation == 1 for job in claimed)

    claim_result, _ = await asyncio.gather(
        state.claim(),
        state.enqueue([IndexJob(document_id="doc-new", operation="delete")]),
    )
    new_job = claim_result or await state.claim()
    assert new_job and new_job.document_id == "doc-new"
    await state.close()


@pytest.mark.asyncio
async def test_enqueue_rejects_persisted_error_or_nonpending_state(tmp_path):
    state = SqliteControlState(str(tmp_path / "control.db"))
    await state.init()
    with pytest.raises(ValueError, match="clean and pending"):
        await state.enqueue(
            [
                IndexJob(
                    document_id="doc-1",
                    operation="upsert",
                    status="error",
                    error_code="ConfidentialAcquisition",
                )
            ]
        )
    await state.close()


@pytest.mark.asyncio
async def test_enqueue_rejects_invalid_operation_before_writing_batch(tmp_path):
    state = SqliteControlState(str(tmp_path / "control.db"))
    await state.init()
    with pytest.raises(ValueError, match="clean and pending"):
        await state.enqueue(
            [
                IndexJob(document_id="doc-valid", operation="upsert"),
                IndexJob(document_id="doc-invalid", operation="parser secret"),
            ]
        )
    assert await state.claim() is None
    await state.close()


@pytest.mark.asyncio
async def test_enqueue_rolls_back_if_sqlite_rejects_part_of_batch(tmp_path):
    state = SqliteControlState(str(tmp_path / "control.db"))
    await state.init()
    db = state._require_db()
    await db.execute(
        """CREATE TRIGGER reject_bad_search_job
           BEFORE INSERT ON search_jobs
           WHEN NEW.document_id = 'doc-bad'
           BEGIN SELECT RAISE(ABORT, 'injected failure'); END"""
    )
    await db.commit()

    with pytest.raises(sqlite3.IntegrityError, match="injected failure"):
        await state.enqueue(
            [
                IndexJob(document_id="doc-good", operation="upsert"),
                IndexJob(document_id="doc-bad", operation="delete"),
            ]
        )

    assert await state.claim() is None
    await state.close()


def test_control_path_rechecks_resolved_target(monkeypatch, tmp_path):
    checks = iter((False, True))
    monkeypatch.setattr(
        control_module, "_is_network_filesystem", lambda _path: next(checks)
    )
    with pytest.raises(ValueError, match="local storage"):
        control_module.require_local_control_path(str(tmp_path / "control.db"))


def test_linux_network_filesystems_are_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(control_module, "_linux_filesystem_type", lambda _path: "nfs4")
    assert control_module._is_network_filesystem(
        tmp_path, os_name="posix", platform="linux"
    )


def test_linux_unknown_filesystem_detection_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(control_module, "_linux_filesystem_type", lambda _path: None)
    assert control_module._is_network_filesystem(
        tmp_path, os_name="posix", platform="linux"
    )


@pytest.mark.parametrize("filesystem", ["tmpfs", "overlay"])
def test_linux_ephemeral_filesystems_are_rejected(monkeypatch, tmp_path, filesystem):
    monkeypatch.setattr(
        control_module, "_linux_filesystem_type", lambda _path: filesystem
    )
    assert control_module._is_network_filesystem(
        tmp_path, os_name="posix", platform="linux"
    )


def test_windows_requires_a_fixed_persistent_volume(monkeypatch, tmp_path):
    class _Kernel32:
        @staticmethod
        def GetDriveTypeW(_anchor):
            return 6  # DRIVE_RAMDISK

    monkeypatch.setattr(
        control_module.ctypes,
        "windll",
        type("_Windll", (), {"kernel32": _Kernel32})(),
        raising=False,
    )
    path = type(tmp_path)("C:/lawhand/control.db")
    assert control_module._is_network_filesystem(path, os_name="nt", platform="win32")
