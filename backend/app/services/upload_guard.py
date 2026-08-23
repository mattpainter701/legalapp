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


# A multipart body carries the file plus boundary lines, part headers, and the
# other form fields. Comparing the whole request length against the file limit
# would reject a file that is exactly at the limit and which the post-read check
# accepts. Allow the same headroom nginx does (client_max_body_size is 55m for a
# 50MB file limit), so this stays a cheap early reject for bodies that cannot
# possibly fit rather than a second, stricter limit.
MULTIPART_OVERHEAD_ALLOWANCE_BYTES = 5 * 1024 * 1024


def reject_oversized_request(request: Request, max_bytes: int, max_mb: int) -> None:
    """Raise 413 when the declared body size cannot possibly fit the limit.

    The authoritative check remains the caller's post-read comparison against
    the actual file bytes. This only avoids materializing a body that is
    already too large to be valid, so it deliberately errs toward accepting.
    """
    declared = request.headers.get("content-length")
    if not declared:
        return
    try:
        length = int(declared)
    except (TypeError, ValueError):
        return
    if length > max_bytes + MULTIPART_OVERHEAD_ALLOWANCE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds maximum size of {max_mb}MB",
        )
