import json
import os
import time
from types import SimpleNamespace

import pytest

from app.services.backup_status import BackupStatusError, read_backup_status


def _payload(now: int, **overrides):
    value = {
        "schema_version": 1,
        "completed_at_epoch": now,
        "status": "ok",
        "offsite": True,
        "components": [
            "legalapp_database",
            "litellm_database",
            "uploads",
            "key_escrow",
        ],
    }
    value.update(overrides)
    return value


def _write(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_accepts_fresh_complete_offsite_backup_status(tmp_path):
    now = int(time.time())
    status = tmp_path / "backup-status.json"
    _write(status, _payload(now))

    assert read_backup_status(str(status), max_age_seconds=7200, now=now) == "ok"


@pytest.mark.parametrize("offset", [-7201, 31])
def test_rejects_stale_or_future_backup_status(tmp_path, offset):
    now = int(time.time())
    status = tmp_path / "backup-status.json"
    _write(status, _payload(now + offset))

    with pytest.raises(BackupStatusError) as exc:
        read_backup_status(str(status), max_age_seconds=7200, now=now)
    assert exc.value.state == "stale"


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "ok"},
        _payload(1, offsite=False),
        _payload(1, completed_at_epoch="soon"),
        _payload(1, components=["legalapp_database"]),
        _payload(
            1,
            components=[
                "legalapp_database",
                "litellm_database",
                "uploads",
                "key_escrow",
                "key_escrow",
            ],
        ),
        _payload(1, unexpected=True),
    ],
)
def test_rejects_malformed_or_incomplete_backup_status(tmp_path, payload):
    status = tmp_path / "backup-status.json"
    _write(status, payload)

    with pytest.raises(BackupStatusError) as exc:
        read_backup_status(str(status), max_age_seconds=7200, now=1)
    assert exc.value.state == "unavailable"


def test_rejects_symlinked_backup_status(tmp_path):
    if not hasattr(os, "symlink"):
        pytest.skip("symlinks unavailable")
    now = int(time.time())
    target = tmp_path / "target.json"
    link = tmp_path / "backup-status.json"
    _write(target, _payload(now))
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is not permitted")

    with pytest.raises(BackupStatusError) as exc:
        read_backup_status(str(link), max_age_seconds=7200, now=now)
    assert exc.value.state == "unavailable"


class _UnavailableDatabase:
    async def __aenter__(self):
        raise RuntimeError("database intentionally unavailable in readiness unit test")

    async def __aexit__(self, exc_type, exc, traceback):
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize("backup_state", ["ok", "stale"])
async def test_public_readiness_reports_non_sensitive_backup_state(
    monkeypatch, backup_state
):
    from app import main as main_module

    monkeypatch.setattr(
        main_module.settings, "BACKUP_STATUS_FILE", "/status/backup-status.json"
    )
    monkeypatch.setattr(main_module.settings, "HEALTH_BACKUP_MAX_AGE_SECONDS", 7200)
    monkeypatch.setattr(
        main_module.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=100, used=10),
    )
    monkeypatch.setattr(
        main_module, "async_session_maker", lambda: _UnavailableDatabase()
    )

    if backup_state == "ok":
        monkeypatch.setattr(main_module, "read_backup_status", lambda *_a, **_k: "ok")
    else:

        def stale_status(*_args, **_kwargs):
            raise BackupStatusError("stale", "proof expired")

        monkeypatch.setattr(main_module, "read_backup_status", stale_status)

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=None)))
    response = await main_module.health_readiness(request)
    payload = json.loads(response.body)

    assert payload["components"]["backups"] == backup_state
    assert "proof expired" not in response.body.decode("utf-8")
