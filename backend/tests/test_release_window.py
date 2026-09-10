"""Workstream D: advisory release-window notice served from a deploy marker.

The deploy script writes ``release-window.json`` beside the host disk status on
the read-only host-status mount for the duration of a deploy and removes it on
exit. The reader must fail closed (missing, malformed, stale, or inconsistent
marker means "no active window") and must never surface host or build detail.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone

import pytest

from app import main as main_module
from app.services.release_window import (
    DEFAULT_MESSAGE,
    MAX_AGE_SECONDS,
    MESSAGE_MAX_CHARS,
    WINDOW_ID_MAX_CHARS,
    ReleaseWindowError,
    read_release_window,
)


def _write_marker(path, **overrides):
    payload = {
        "active": True,
        "window_id": "abc123def456",
        "message": "Polishing things; saved work is safe.",
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    payload.update(overrides)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# ── Reader unit tests ─────────────────────────────────────────────────────────


def test_missing_marker_means_no_window(tmp_path):
    with pytest.raises(ReleaseWindowError):
        read_release_window(str(tmp_path / "release-window.json"))


def test_valid_marker_returns_only_public_fields(tmp_path):
    result = read_release_window(str(_write_marker(tmp_path / "release-window.json")))
    assert result == {
        "active": True,
        "window_id": "abc123def456",
        "message": "Polishing things; saved work is safe.",
    }


def test_default_message_applies_when_message_omitted(tmp_path):
    marker = _write_marker(tmp_path / "release-window.json", message=None)
    result = read_release_window(str(marker))
    assert result["active"] is True
    assert result["message"] == DEFAULT_MESSAGE


def test_oversized_message_is_truncated(tmp_path):
    marker = _write_marker(tmp_path / "release-window.json", message="x" * 500)
    result = read_release_window(str(marker))
    assert len(result["message"]) == MESSAGE_MAX_CHARS


def test_marker_without_started_at_is_accepted(tmp_path):
    # started_at is optional: older writers may omit it; the EXIT trap is the
    # primary cleanup, the age check is only a stranded-marker backstop.
    marker = _write_marker(tmp_path / "release-window.json", started_at=None)
    assert read_release_window(str(marker))["active"] is True


@pytest.mark.parametrize(
    "overrides",
    [
        {"active": False},
        {"window_id": ""},
        {"window_id": None},
        {"window_id": "x" * (WINDOW_ID_MAX_CHARS + 1)},
        {"window_id": 123},
        {"message": ""},
        {"message": "   "},
        {"message": 42},
    ],
    ids=[
        "inactive-flag",
        "empty-window-id",
        "null-window-id",
        "oversized-window-id",
        "non-string-window-id",
        "empty-message",
        "blank-message",
        "non-string-message",
    ],
)
def test_invalid_markers_fail_closed(tmp_path, overrides):
    marker = _write_marker(tmp_path / "release-window.json", **overrides)
    with pytest.raises(ReleaseWindowError):
        read_release_window(str(marker))


def test_marker_without_active_flag_fails_closed(tmp_path):
    marker = tmp_path / "release-window.json"
    marker.write_text(
        json.dumps(
            {
                "window_id": "abc123def456",
                "message": "hi",
                "started_at": datetime.now(timezone.utc).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ReleaseWindowError):
        read_release_window(str(marker))


def test_malformed_json_fails_closed(tmp_path):
    marker = tmp_path / "release-window.json"
    marker.write_text("{not json", encoding="utf-8")
    with pytest.raises(ReleaseWindowError):
        read_release_window(str(marker))


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="creating symlinks requires privilege on Windows; covered in CI on Linux",
)
def test_symlink_marker_is_refused(tmp_path):
    target = _write_marker(tmp_path / "real.json")
    link = tmp_path / "release-window.json"
    link.symlink_to(target)
    with pytest.raises(ReleaseWindowError):
        read_release_window(str(link))


def test_stale_marker_expires(tmp_path):
    started = datetime.now(timezone.utc) - timedelta(seconds=MAX_AGE_SECONDS + 1)
    marker = _write_marker(
        tmp_path / "release-window.json", started_at=started.isoformat()
    )
    with pytest.raises(ReleaseWindowError):
        read_release_window(str(marker))


def test_future_marker_beyond_skew_is_refused(tmp_path):
    started = datetime.now(timezone.utc) + timedelta(seconds=3600)
    marker = _write_marker(
        tmp_path / "release-window.json", started_at=started.isoformat()
    )
    with pytest.raises(ReleaseWindowError):
        read_release_window(str(marker))


# ── Endpoint tests ────────────────────────────────────────────────────────────


@pytest.fixture
def host_status_dir(tmp_path, monkeypatch):
    status_dir = tmp_path / "host-status"
    status_dir.mkdir()
    monkeypatch.setattr(
        main_module.settings,
        "HOST_DISK_STATUS_FILE",
        str(status_dir / "disk-status.json"),
    )
    return status_dir


async def test_endpoint_inactive_without_host_status_setting(monkeypatch, client):
    monkeypatch.setattr(main_module.settings, "HOST_DISK_STATUS_FILE", "")
    response = await client.get("/api/release-window")
    assert response.status_code == 200
    assert response.json() == {"active": False, "window_id": None, "message": None}


async def test_endpoint_reports_active_window(host_status_dir, client):
    _write_marker(host_status_dir / "release-window.json")
    response = await client.get("/api/release-window")
    assert response.status_code == 200
    body = response.json()
    assert body["active"] is True
    assert body["window_id"] == "abc123def456"
    assert body["message"] == "Polishing things; saved work is safe."


async def test_endpoint_fails_closed_on_malformed_marker(host_status_dir, client):
    (host_status_dir / "release-window.json").write_text("nope", encoding="utf-8")
    response = await client.get("/api/release-window")
    assert response.status_code == 200
    assert response.json()["active"] is False


async def test_endpoint_fails_closed_when_marker_missing(host_status_dir, client):
    response = await client.get("/api/release-window")
    assert response.status_code == 200
    assert response.json()["active"] is False
