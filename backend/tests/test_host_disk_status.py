import json
import os
import time

import pytest

from app.services.host_disk_status import HostDiskStatusError, read_host_disk_status


def _payload(now: int, **overrides):
    value = {
        "schema_version": 1,
        "checked_at_epoch": now,
        "status": "ok",
        "filesystems_checked": 3,
        "paths_checked": 7,
        "max_used_percent": 42,
        "threshold_percent": 85,
        "errors_count": 0,
    }
    value.update(overrides)
    return value


def _write(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_accepts_fresh_consistent_host_disk_status(tmp_path):
    now = int(time.time())
    status = tmp_path / "disk-status.json"
    _write(status, _payload(now))

    assert read_host_disk_status(str(status), max_age_seconds=180, now=now) == "ok"


def test_accepts_probe_fallback_with_no_resolved_filesystem(tmp_path):
    now = int(time.time())
    status = tmp_path / "disk-status.json"
    _write(
        status,
        _payload(
            now,
            status="unavailable",
            filesystems_checked=0,
            paths_checked=4,
            max_used_percent=0,
            errors_count=1,
        ),
    )

    assert (
        read_host_disk_status(str(status), max_age_seconds=180, now=now)
        == "unavailable"
    )


@pytest.mark.parametrize("offset", [-181, 31])
def test_rejects_stale_or_future_host_disk_status(tmp_path, offset):
    now = int(time.time())
    status = tmp_path / "disk-status.json"
    _write(status, _payload(now + offset))

    with pytest.raises(HostDiskStatusError) as exc:
        read_host_disk_status(str(status), max_age_seconds=180, now=now)
    assert exc.value.state == "stale"


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "ok"},
        _payload(1, max_used_percent=90),
        _payload(1, filesystems_checked=0),
        _payload(1, checked_at_epoch="soon"),
        _payload(1, unexpected=True),
    ],
)
def test_rejects_malformed_or_inconsistent_status(tmp_path, payload):
    status = tmp_path / "disk-status.json"
    _write(status, payload)

    with pytest.raises(HostDiskStatusError) as exc:
        read_host_disk_status(str(status), max_age_seconds=180, now=1)
    assert exc.value.state == "unavailable"


def test_rejects_symlinked_status_artifact(tmp_path):
    if not hasattr(os, "symlink"):
        pytest.skip("symlinks unavailable")
    now = int(time.time())
    target = tmp_path / "target.json"
    link = tmp_path / "disk-status.json"
    _write(target, _payload(now))
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is not permitted")

    with pytest.raises(HostDiskStatusError) as exc:
        read_host_disk_status(str(link), max_age_seconds=180, now=now)
    assert exc.value.state == "unavailable"
