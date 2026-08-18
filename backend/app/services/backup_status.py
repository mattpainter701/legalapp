"""Strict reader for the non-sensitive off-site backup status artifact."""

from __future__ import annotations

import json
import os
import stat
import time
from pathlib import Path


EXPECTED_KEYS = {
    "schema_version",
    "completed_at_epoch",
    "status",
    "offsite",
    "components",
}
EXPECTED_COMPONENTS = {
    "legalapp_database",
    "litellm_database",
    "uploads",
    "key_escrow",
}


class BackupStatusError(RuntimeError):
    def __init__(self, state: str, message: str):
        super().__init__(message)
        self.state = state


def read_backup_status(
    filename: str, *, max_age_seconds: int, now: float | None = None
) -> str:
    """Return ``ok`` only for a fresh, complete, off-site backup proof."""

    path = Path(filename)
    if path.is_symlink():
        raise BackupStatusError("unavailable", "backup status may not be a symlink")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise BackupStatusError("unavailable", "backup status is unreadable") from exc
    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode) or not 1 <= file_stat.st_size <= 4096:
            raise BackupStatusError(
                "unavailable", "backup status is not a small regular file"
            )
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            descriptor = -1
            payload = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BackupStatusError("unavailable", "backup status is malformed") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)

    if not isinstance(payload, dict) or set(payload) != EXPECTED_KEYS:
        raise BackupStatusError("unavailable", "backup status schema is invalid")
    if payload.get("schema_version") != 1:
        raise BackupStatusError("unavailable", "backup status version is unsupported")
    if payload.get("status") != "ok" or payload.get("offsite") is not True:
        raise BackupStatusError(
            "unavailable", "backup status does not prove an off-site success"
        )
    components = payload.get("components")
    if (
        not isinstance(components, list)
        or any(not isinstance(value, str) for value in components)
        or set(components) != EXPECTED_COMPONENTS
        or len(components) != len(EXPECTED_COMPONENTS)
    ):
        raise BackupStatusError("unavailable", "backup component proof is incomplete")
    completed_at = payload.get("completed_at_epoch")
    if type(completed_at) is not int or not 1 <= completed_at <= 4_102_444_800:
        raise BackupStatusError("unavailable", "backup completion time is invalid")

    current = time.time() if now is None else now
    age = current - completed_at
    if age < -30:
        raise BackupStatusError("stale", "backup completion time is in the future")
    if max_age_seconds < 300 or age > max_age_seconds:
        raise BackupStatusError("stale", "off-site backup proof is stale")
    return "ok"
