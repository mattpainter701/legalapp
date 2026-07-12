"""Bounded, diagnostic wait for the internal production readiness endpoint."""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import BinaryIO, TextIO


READINESS_URL = "http://127.0.0.1:8000/health/readiness"
MAX_ATTEMPTS = 30
RETRY_DELAY_SECONDS = 2.0
REQUEST_TIMEOUT_SECONDS = 5.0
MAX_VISIBLE_BODY_BYTES = 4096


def _read_limited(stream: BinaryIO) -> bytes:
    body = stream.read(MAX_VISIBLE_BODY_BYTES + 1)
    if len(body) <= MAX_VISIBLE_BODY_BYTES:
        return body
    return body[:MAX_VISIBLE_BODY_BYTES] + b"...[truncated]"


def _visible_body(body: bytes) -> str:
    decoded = body.decode("utf-8", errors="replace")
    compact = " ".join(decoded.split())
    return compact or "<empty>"


def _is_ready(status_code: int, body: bytes) -> bool:
    if status_code != 200:
        return False
    try:
        payload = json.loads(body)
    except (TypeError, ValueError, UnicodeDecodeError):
        return False
    return isinstance(payload, dict) and payload.get("status") == "ok"


def wait_for_readiness(
    *,
    url: str = READINESS_URL,
    max_attempts: int = MAX_ATTEMPTS,
    retry_delay_seconds: float = RETRY_DELAY_SECONDS,
    request_timeout_seconds: float = REQUEST_TIMEOUT_SECONDS,
    opener: Callable[..., object] = urllib.request.urlopen,
    sleeper: Callable[[float], None] = time.sleep,
    diagnostics: TextIO = sys.stderr,
) -> bool:
    """Poll readiness until healthy or the fixed attempt budget is exhausted."""

    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")
    if retry_delay_seconds < 0 or request_timeout_seconds <= 0:
        raise ValueError("readiness timing values are invalid")

    for attempt in range(1, max_attempts + 1):
        try:
            response = opener(url, timeout=request_timeout_seconds)
            try:
                status_code = int(response.getcode())
                body = _read_limited(response)
            finally:
                response.close()
            if _is_ready(status_code, body):
                return True
            detail = f"HTTP {status_code}; body={_visible_body(body)}"
        except urllib.error.HTTPError as exc:
            try:
                body = _read_limited(exc)
            finally:
                exc.close()
            detail = f"HTTP {exc.code}; body={_visible_body(body)}"
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"

        print(
            f"Readiness attempt {attempt}/{max_attempts} failed: {detail}",
            file=diagnostics,
            flush=True,
        )
        if attempt < max_attempts:
            sleeper(retry_delay_seconds)

    return False


def main() -> int:
    if not wait_for_readiness():
        return 1
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
