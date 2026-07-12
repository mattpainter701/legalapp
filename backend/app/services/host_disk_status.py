"""Strict reader for the aggregate host-side disk monitor artifact."""

from __future__ import annotations

import json
import os
import stat
import time
from pathlib import Path


EXPECTED_KEYS = {
    "schema_version",
    "checked_at_epoch",
    "status",
    "filesystems_checked",
    "paths_checked",
    "max_used_percent",
    "threshold_percent",
    "errors_count",
}


class HostDiskStatusError(RuntimeError):
    def __init__(self, state: str, message: str):
        super().__init__(message)
        self.state = state


def _bounded_int(payload: dict, key: str, minimum: int, maximum: int) -> int:
    value = payload.get(key)
    if type(value) is not int or not minimum <= value <= maximum:
        raise HostDiskStatusError("unavailable", f"invalid host disk field: {key}")
    return value


def read_host_disk_status(
    filename: str, *, max_age_seconds: int, now: float | None = None
) -> str:
    path = Path(filename)
    if path.is_symlink():
        raise HostDiskStatusError(
            "unavailable", "host disk status may not be a symlink"
        )
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise HostDiskStatusError(
            "unavailable", "host disk status is unreadable"
        ) from exc
    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode) or not 1 <= file_stat.st_size <= 4096:
            raise HostDiskStatusError(
                "unavailable", "host disk status is not a small regular file"
            )
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            descriptor = -1
            payload = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise HostDiskStatusError(
            "unavailable", "host disk status is malformed"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)

    if not isinstance(payload, dict) or set(payload) != EXPECTED_KEYS:
        raise HostDiskStatusError("unavailable", "host disk status schema is invalid")
    if payload.get("schema_version") != 1:
        raise HostDiskStatusError(
            "unavailable", "host disk status version is unsupported"
        )
    checked_at = _bounded_int(payload, "checked_at_epoch", 1, 4_102_444_800)
    filesystems = _bounded_int(payload, "filesystems_checked", 0, 10_000)
    paths = _bounded_int(payload, "paths_checked", max(filesystems, 1), 100_000)
    _bounded_int(payload, "max_used_percent", 0, 100)
    threshold = _bounded_int(payload, "threshold_percent", 1, 100)
    errors = _bounded_int(payload, "errors_count", 0, paths)
    status_value = payload.get("status")
    if status_value not in {"ok", "full", "unavailable"}:
        raise HostDiskStatusError("unavailable", "host disk status value is invalid")
    if filesystems == 0 and status_value != "unavailable":
        raise HostDiskStatusError(
            "unavailable", "host disk status fields are inconsistent"
        )
    if status_value == "ok" and (
        errors != 0 or payload["max_used_percent"] >= threshold
    ):
        raise HostDiskStatusError(
            "unavailable", "host disk status fields are inconsistent"
        )
    if status_value == "full" and payload["max_used_percent"] < threshold:
        raise HostDiskStatusError("unavailable", "host disk full state is inconsistent")
    if status_value == "unavailable" and errors == 0:
        raise HostDiskStatusError(
            "unavailable", "host disk unavailable state is inconsistent"
        )

    current = time.time() if now is None else now
    age = current - checked_at
    if age < -30:
        raise HostDiskStatusError(
            "stale", "host disk status timestamp is in the future"
        )
    if max_age_seconds < 60 or age > max_age_seconds:
        raise HostDiskStatusError("stale", "host disk status is stale")
    return status_value
