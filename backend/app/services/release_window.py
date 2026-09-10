"""Strict reader for the advisory deploy release-window marker.

``deploy_prod.sh`` writes ``release-window.json`` beside the host disk status
on the read-only host-status mount at the start of every deploy and removes it
on exit (EXIT trap), so logged-in users can see a kindly worded heads-up while
a release is in flight. The reader is deliberately fail-closed: a missing,
malformed, stale, or inconsistent marker means "no active window" — an
advisory banner must never turn into an error a user can see.
"""

from __future__ import annotations

import json
import os
import stat
import time
from datetime import datetime, timezone
from pathlib import Path

MESSAGE_MAX_CHARS = 300
WINDOW_ID_MAX_CHARS = 64
# A deploy takes minutes. A hard-killed deploy cannot run its cleanup trap, so
# a marker auto-expires long after any real window instead of lingering.
MAX_AGE_SECONDS = 2 * 60 * 60
# Tolerate small clock skew between the deploy host and the backend container.
FUTURE_SKEW_SECONDS = 60

DEFAULT_MESSAGE = (
    "We're giving LawHand a quick polish to keep everything running "
    "smoothly. Over the next few minutes you may briefly see a "
    "\u201cWe'll be right back\u201d message \u2014 that's us, hard at "
    "work. Everything you've saved is safe and will be right where you "
    "left it."
)


class ReleaseWindowError(RuntimeError):
    """Marker is unreadable or invalid; the caller stays inactive."""


def read_release_window(filename: str, *, now: float | None = None) -> dict:
    """Return the active-window notice or raise ReleaseWindowError.

    Only ``active``, ``window_id``, and ``message`` cross this boundary — no
    hostnames, paths, or build detail ride along to the browser.
    """
    path = Path(filename)
    if path.is_symlink():
        raise ReleaseWindowError("release window marker may not be a symlink")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ReleaseWindowError("release window marker is unreadable") from exc
    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode) or not 1 <= file_stat.st_size <= 4096:
            raise ReleaseWindowError(
                "release window marker is not a small regular file"
            )
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            descriptor = -1
            payload = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseWindowError("release window marker is malformed") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)

    if not isinstance(payload, dict) or payload.get("active") is not True:
        raise ReleaseWindowError("release window marker is not active")

    window_id = payload.get("window_id")
    if (
        not isinstance(window_id, str)
        or not window_id
        or len(window_id) > WINDOW_ID_MAX_CHARS
    ):
        raise ReleaseWindowError("release window id is invalid")

    message = payload.get("message")
    if message is None:
        message = DEFAULT_MESSAGE
    if not isinstance(message, str) or not message.strip():
        raise ReleaseWindowError("release window message is invalid")

    started_at = payload.get("started_at")
    if isinstance(started_at, str) and started_at:
        try:
            started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ReleaseWindowError("release window start is invalid") from exc
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        current = time.time() if now is None else now
        age = current - started.timestamp()
        if age < -FUTURE_SKEW_SECONDS or age > MAX_AGE_SECONDS:
            raise ReleaseWindowError(
                "release window marker is outside its valid lifetime"
            )

    return {
        "active": True,
        "window_id": window_id,
        "message": message[:MESSAGE_MAX_CHARS],
    }
