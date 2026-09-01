"""Loopback-only authenticated HTTP gateway for outbound agent relay calls."""

from __future__ import annotations

import asyncio
import hmac
import ipaddress
import json
from dataclasses import asdict
from datetime import datetime
from typing import Any

from clarity_agent.search_engine import LocalSearchEngine, SearchFilters, SearchRequest

MAX_GATEWAY_BODY_BYTES = 64 * 1024
MAX_HEADER_BYTES = 16 * 1024


class LocalQueryGateway:
    def __init__(
        self,
        engine: LocalSearchEngine,
        token: str,
        *,
        host: str = "127.0.0.1",
        port: int = 8765,
    ):
        if host not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("query gateway must bind to loopback")
        if len(token.encode()) < 32:
            raise ValueError("query gateway token must contain at least 32 bytes")
        # Port zero is useful for collision-free tests and operator probes;
        # the kernel replaces it with a concrete loopback port at bind time.
        if not 0 <= int(port) <= 65535:
            raise ValueError("query gateway port is invalid")
        self.engine = engine
        self.token = token
        self.host = host
        self.port = int(port)
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        if self._server is None:
            self._server = await asyncio.start_server(
                self._handle_connection, self.host, self.port, limit=MAX_HEADER_BYTES
            )
            sockets = self._server.sockets or []
            if any(
                not ipaddress.ip_address(socket.getsockname()[0]).is_loopback
                for socket in sockets
            ):
                await self.close()
                raise RuntimeError("query gateway resolved outside loopback")
            if sockets:
                self.port = int(sockets[0].getsockname()[1])

    async def close(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            status, payload = await asyncio.wait_for(
                self._read_and_dispatch(reader), timeout=12
            )
        except asyncio.TimeoutError:
            status, payload = 408, {"error": "request_timeout"}
        except (ValueError, json.JSONDecodeError):
            status, payload = 400, {"error": "invalid_request"}
        except Exception:
            status, payload = 503, {"error": "search_unavailable"}
        body = json.dumps(payload, separators=(",", ":")).encode()
        reason = {
            200: "OK",
            400: "Bad Request",
            401: "Unauthorized",
            404: "Not Found",
            405: "Method Not Allowed",
            408: "Request Timeout",
            413: "Content Too Large",
            503: "Service Unavailable",
        }.get(status, "Error")
        writer.write(
            f"HTTP/1.1 {status} {reason}\r\n".encode()
            + b"Content-Type: application/json\r\n"
            + f"Content-Length: {len(body)}\r\n".encode()
            + b"Cache-Control: no-store\r\nConnection: close\r\n\r\n"
            + body
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async def _read_and_dispatch(
        self, reader: asyncio.StreamReader
    ) -> tuple[int, dict[str, Any]]:
        request_line = (await reader.readline()).decode("ascii", "strict").strip()
        parts = request_line.split()
        if len(parts) != 3 or not parts[2].startswith("HTTP/1."):
            raise ValueError("invalid request line")
        method, path, _ = parts
        headers: dict[str, str] = {}
        header_bytes = len(request_line)
        while True:
            line = await reader.readline()
            header_bytes += len(line)
            if header_bytes > MAX_HEADER_BYTES:
                return 413, {"error": "headers_too_large"}
            if line in {b"\r\n", b"\n", b""}:
                break
            name, separator, value = line.decode("ascii", "strict").partition(":")
            if not separator:
                raise ValueError("invalid header")
            headers[name.strip().lower()] = value.strip()
        supplied = headers.get("authorization", "")
        expected = f"Bearer {self.token}"
        if not hmac.compare_digest(supplied.encode(), expected.encode()):
            return 401, {"error": "unauthorized"}
        if "transfer-encoding" in headers:
            return 400, {"error": "transfer_encoding_not_supported"}
        length = int(headers.get("content-length", "0"))
        if length < 0 or length > MAX_GATEWAY_BODY_BYTES:
            return 413, {"error": "body_too_large"}
        body = await reader.readexactly(length) if length else b""

        if method == "GET" and path == "/v1/health":
            return 200, asdict(await self.engine.health())
        if method != "POST":
            return 405, {"error": "method_not_allowed"}
        if path != "/v1/search":
            return 404, {"error": "not_found"}
        data = json.loads(body or b"{}")
        response = await self.engine.search(self._parse_search(data))
        return 200, asdict(response)

    @staticmethod
    def _parse_search(data: object) -> SearchRequest:
        if not isinstance(data, dict):
            raise ValueError("request must be an object")
        allowed = {
            "query",
            "acl_tokens",
            "filters",
            "limit",
            "offset",
            "highlight",
            "timeout_ms",
        }
        if set(data) - allowed:
            raise ValueError("unknown request field")
        raw_filters = data.get("filters") or {}
        if not isinstance(raw_filters, dict):
            raise ValueError("filters must be an object")
        allowed_filters = {
            "share_ids",
            "matter_ids",
            "extensions",
            "document_ids",
            "modified_after",
            "modified_before",
        }
        if set(raw_filters) - allowed_filters:
            raise ValueError("unknown filter field")

        def values(name: str) -> tuple[str, ...]:
            value = raw_filters.get(name) or []
            if not isinstance(value, list) or len(value) > 100:
                raise ValueError("filter must be a bounded list")
            result = tuple(str(item) for item in value if str(item))
            if any(len(item) > 512 for item in result):
                raise ValueError("filter value is too long")
            return result

        def instant(name: str) -> datetime | None:
            value = raw_filters.get(name)
            return (
                datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                if value
                else None
            )

        acl_tokens = data.get("acl_tokens") or []
        if not isinstance(acl_tokens, list) or not acl_tokens or len(acl_tokens) > 512:
            raise ValueError("acl_tokens must be a bounded non-empty list")
        tokens = tuple(str(item) for item in acl_tokens)
        if any(not item or len(item) > 256 for item in tokens):
            raise ValueError("invalid ACL token")
        return SearchRequest(
            query=str(data.get("query") or ""),
            acl_tokens=tokens,
            filters=SearchFilters(
                share_ids=values("share_ids"),
                matter_ids=values("matter_ids"),
                extensions=values("extensions"),
                document_ids=values("document_ids"),
                modified_after=instant("modified_after"),
                modified_before=instant("modified_before"),
            ),
            limit=int(data.get("limit", 20)),
            offset=int(data.get("offset", 0)),
            highlight=bool(data.get("highlight", True)),
            timeout_ms=int(data.get("timeout_ms", 2_000)),
        )
