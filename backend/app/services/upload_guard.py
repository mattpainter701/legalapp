"""Reject oversized uploads before their bytes are materialized in memory.

``await file.read()`` returns the whole body as a single ``bytes`` object, so
checking the length afterwards means the process has already paid the memory
cost of a file it is about to refuse. With four API workers on a 4G limit, and
nginx admitting bodies up to 55M, that is worth avoiding on the way in.

The Content-Length header is advisory -- a client may omit or understate it --
so this is a cheap early reject, not the only check. Callers still validate the
real length after reading.
"""

from __future__ import annotations

from fastapi import HTTPException, Request


def reject_oversized_request(request: Request, max_bytes: int, max_mb: int) -> None:
    """Raise 413 when the declared body size already exceeds the limit."""
    declared = request.headers.get("content-length")
    if not declared:
        return
    try:
        length = int(declared)
    except (TypeError, ValueError):
        return
    if length > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds maximum size of {max_mb}MB",
        )
