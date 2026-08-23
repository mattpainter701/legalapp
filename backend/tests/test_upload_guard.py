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


@pytest.mark.asyncio
async def test_document_upload_refuses_an_oversized_declared_body(client):
    """Exercises the guard at its call site.

    The multipart body is valid and small; only the declared Content-Length is
    oversized. A 413 proves the header check ran and short-circuited the
    handler before `await file.read()` pulled the body into memory as a single
    bytes object.

    Note the guard cannot prevent FastAPI parsing the multipart form -- that
    happens while resolving the `UploadFile` dependency, before the function
    body runs. It spools to a temp file rather than RAM, so the allocation this
    guard avoids is the one that mattered.
    """
    response = await client.post(
        "/api/documents/upload",
        files={"file": ("big.pdf", b"%PDF-1.4 tiny", "application/pdf")},
        headers={"content-length": str(MAX_BYTES * 4)},
    )
    assert response.status_code == 413
    assert "50MB" in response.json()["detail"]


@pytest.mark.asyncio
async def test_document_upload_accepts_a_body_within_the_allowance(client):
    """The same call site must not reject a normal upload."""
    response = await client.post(
        "/api/documents/upload",
        files={"file": ("small.pdf", b"%PDF-1.4 tiny", "application/pdf")},
    )
    assert response.status_code != 413
