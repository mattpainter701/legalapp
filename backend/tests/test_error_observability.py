"""Focused tests for request/error ID observability surfaces."""

import json
import uuid

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request

from app import main as app_main
from app.middleware.request_id import RequestIdMiddleware


def _request_with_id(request_id: str) -> Request:
    request = Request(
        scope={
            "type": "http",
            "method": "GET",
            "path": "/__test__",
            "scheme": "http",
            "query_string": b"",
            "headers": [],
            "client": ("test", 12345),
            "server": ("test", 12345),
            "state": {},
        }
    )
    request.state.request_id = request_id
    return request


@pytest.mark.asyncio
async def test_request_id_header_on_success():
    test_app = FastAPI()
    test_app.add_middleware(RequestIdMiddleware)

    @test_app.get("/ok")
    async def ok():
        return {"ok": True}

    async with AsyncClient(
        transport=ASGITransport(app=test_app),
        base_url="http://test",
    ) as client:
        response = await client.get("/ok", headers={"X-Request-ID": "req-123"})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "req-123"


@pytest.mark.asyncio
async def test_http_exception_includes_request_id(monkeypatch):
    async def _fake_capture(_request, _exc, _status_code, **kwargs):
        return None

    monkeypatch.setattr(app_main, "_capture_exception_to_errorlog", _fake_capture)

    response = await app_main.http_exception_handler(
        _request_with_id("req-http"),
        HTTPException(status_code=403, detail="feature blocked"),
    )
    assert response.status_code == 403
    body = json.loads(response.body.decode())

    assert body["detail"] == "feature blocked"
    assert body["request_id"] == "req-http"
    assert response.headers["X-Request-ID"] == "req-http"


@pytest.mark.asyncio
async def test_generic_error_includes_request_id_and_error_id(
    monkeypatch,
):
    error_id = uuid.uuid4()

    async def _fake_capture(_request, _exc, _status_code, **kwargs):
        return error_id

    monkeypatch.setattr(app_main, "_capture_exception_to_errorlog", _fake_capture)

    response = await app_main.generic_exception_handler(
        _request_with_id("req-test"), RuntimeError("boom")
    )
    body = json.loads(response.body.decode())

    assert response.status_code == 500
    assert body["detail"] == "Something went wrong"
    assert body["request_id"] == "req-test"
    assert body["error_id"] == str(error_id)
    assert response.headers["X-Request-ID"] == "req-test"
