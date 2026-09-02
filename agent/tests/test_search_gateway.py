from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from clarity_agent.opensearch_engine import SearchUnavailableError
from clarity_agent.search_engine import EngineHealth, SearchResponse
from clarity_agent.search_gateway import LocalQueryGateway

TOKEN = "a" * 32


class FakeEngine:
    def __init__(self):
        self.requests = []

    async def health(self):
        return EngineHealth("healthy", "fake", 1, "idx")

    async def search(self, request):
        self.requests.append(request)
        return SearchResponse((), 0, 1, False, "fake", 1)


async def _request(port: int, raw: bytes) -> tuple[int, dict]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(raw)
    await writer.drain()
    response = await reader.read()
    writer.close()
    await writer.wait_closed()
    head, body = response.split(b"\r\n\r\n", 1)
    return int(head.split()[1]), json.loads(body)


def test_gateway_refuses_external_bind_and_weak_secret():
    with pytest.raises(ValueError, match="loopback"):
        LocalQueryGateway(FakeEngine(), TOKEN, host="0.0.0.0")
    with pytest.raises(ValueError, match="32"):
        LocalQueryGateway(FakeEngine(), "weak")


@pytest.mark.asyncio
async def test_gateway_requires_auth_and_forwards_bounded_acl_query():
    engine = FakeEngine()
    gateway = LocalQueryGateway(engine, TOKEN, port=0)
    await gateway.start()
    try:
        status, _ = await _request(
            gateway.port, b"GET /v1/health HTTP/1.1\r\nHost: localhost\r\n\r\n"
        )
        assert status == 401
        body = json.dumps(
            {
                "query": '"summary judgment" AND granted',
                "acl_tokens": ["sid:1"],
                "filters": {"share_ids": ["share-1"]},
                "limit": 10,
            }
        ).encode()
        raw = (
            b"POST /v1/search HTTP/1.1\r\nHost: localhost\r\n"
            + f"Authorization: Bearer {TOKEN}\r\nContent-Length: {len(body)}\r\n\r\n".encode()
            + body
        )
        status, response = await _request(gateway.port, raw)
        assert status == 200 and response["engine"] == "fake"
        assert engine.requests[0].acl_tokens == ("sid:1",)
        assert engine.requests[0].filters.share_ids == ("share-1",)
    finally:
        await gateway.close()


@pytest.mark.asyncio
async def test_gateway_rejects_unknown_fields_without_querying():
    engine = FakeEngine()
    gateway = LocalQueryGateway(engine, TOKEN, port=0)
    await gateway.start()
    try:
        body = b'{"query":"x","acl_tokens":["a"],"corpus":"send-to-saas"}'
        raw = (
            b"POST /v1/search HTTP/1.1\r\nHost: localhost\r\n"
            + f"Authorization: Bearer {TOKEN}\r\nContent-Length: {len(body)}\r\n\r\n".encode()
            + body
        )
        status, _ = await _request(gateway.port, raw)
        assert status == 400
        assert engine.requests == []
    finally:
        await gateway.close()


@pytest.mark.asyncio
async def test_gateway_reports_search_unavailable_when_engine_fails_closed():
    engine = FakeEngine()

    async def unavailable(_request):
        raise SearchUnavailableError("rebuild quarantine")

    engine.search = unavailable
    gateway = LocalQueryGateway(engine, TOKEN, port=0)
    await gateway.start()
    try:
        body = b'{"query":"x","acl_tokens":["a"]}'
        raw = (
            b"POST /v1/search HTTP/1.1\r\nHost: localhost\r\n"
            + f"Authorization: Bearer {TOKEN}\r\nContent-Length: {len(body)}\r\n\r\n".encode()
            + body
        )
        status, response = await _request(gateway.port, raw)
        assert status == 503
        # Distinct from a plain outage: quarantine will not clear on retry, so
        # the relay must be able to tell the two apart.
        assert response == {"error": "search_quarantined"}
    finally:
        await gateway.close()


@pytest.mark.asyncio
async def test_gateway_reports_a_rejected_query_as_a_client_error():
    """A malformed query string is the caller's mistake, not an outage."""
    engine = FakeEngine()

    async def rejected(_request):
        request = httpx.Request("POST", "http://127.0.0.1:9200/_search")
        raise httpx.HTTPStatusError(
            "parse failure",
            request=request,
            response=httpx.Response(400, request=request),
        )

    engine.search = rejected
    gateway = LocalQueryGateway(engine, TOKEN, port=0)
    await gateway.start()
    try:
        body = b'{"query":"unbalanced AND","acl_tokens":["a"]}'
        raw = (
            b"POST /v1/search HTTP/1.1\r\nHost: localhost\r\n"
            + f"Authorization: Bearer {TOKEN}\r\nContent-Length: {len(body)}\r\n\r\n".encode()
            + body
        )
        status, response = await _request(gateway.port, raw)
        assert status == 400
        assert response == {"error": "invalid_query"}
    finally:
        await gateway.close()


@pytest.mark.asyncio
async def test_gateway_still_reports_a_real_engine_outage_as_unavailable():
    engine = FakeEngine()

    async def down(_request):
        raise httpx.ConnectError("connection refused")

    engine.search = down
    gateway = LocalQueryGateway(engine, TOKEN, port=0)
    await gateway.start()
    try:
        body = b'{"query":"x","acl_tokens":["a"]}'
        raw = (
            b"POST /v1/search HTTP/1.1\r\nHost: localhost\r\n"
            + f"Authorization: Bearer {TOKEN}\r\nContent-Length: {len(body)}\r\n\r\n".encode()
            + body
        )
        status, response = await _request(gateway.port, raw)
        assert status == 503
        assert response == {"error": "search_unavailable"}
    finally:
        await gateway.close()
