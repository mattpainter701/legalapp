"""Early rejection of oversized uploads.

The guard exists so a body that cannot possibly be valid is refused before
`await file.read()` materializes it. It must not become a second, stricter
limit: a multipart body carries boundary lines and part headers on top of the
file, so a file exactly at the documented maximum has a request length above it.
"""

import pytest
from fastapi import HTTPException
from starlette.datastructures import Headers
from starlette.requests import Request

from app.services.upload_guard import (
    MULTIPART_OVERHEAD_ALLOWANCE_BYTES,
    reject_oversized_request,
)

MAX_MB = 50
MAX_BYTES = MAX_MB * 1024 * 1024


def _request(content_length: str | None) -> Request:
    raw = []
    if content_length is not None:
        raw.append((b"content-length", content_length.encode()))
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/upload",
        "headers": Headers(raw=raw).raw,
    }
    return Request(scope)


def test_a_file_at_the_limit_survives_multipart_overhead():
    # The file is exactly at the limit; the request carries part headers too.
    reject_oversized_request(_request(str(MAX_BYTES + 4096)), MAX_BYTES, MAX_MB)


def test_a_clearly_oversized_body_is_refused_before_it_is_read():
    with pytest.raises(HTTPException) as excinfo:
        reject_oversized_request(_request(str(MAX_BYTES * 3)), MAX_BYTES, MAX_MB)
    assert excinfo.value.status_code == 413
    assert "50MB" in excinfo.value.detail


def test_the_allowance_boundary_is_inclusive():
    at_edge = MAX_BYTES + MULTIPART_OVERHEAD_ALLOWANCE_BYTES
    reject_oversized_request(_request(str(at_edge)), MAX_BYTES, MAX_MB)
    with pytest.raises(HTTPException):
        reject_oversized_request(_request(str(at_edge + 1)), MAX_BYTES, MAX_MB)


@pytest.mark.parametrize("header", [None, "", "not-a-number"])
def test_an_absent_or_unusable_header_defers_to_the_post_read_check(header):
    reject_oversized_request(_request(header), MAX_BYTES, MAX_MB)
