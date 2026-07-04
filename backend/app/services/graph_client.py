"""Microsoft Graph helpers built on the shared provider HTTP layer."""

from __future__ import annotations

from typing import Any

import httpx

from app.services.provider_http import provider_request

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"


def graph_url(path: str) -> str:
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return f"{GRAPH_BASE_URL}/{path.lstrip('/')}"


async def graph_request(
    method: str,
    path: str,
    *,
    token: str,
    client: httpx.AsyncClient | None = None,
    **kwargs: Any,
) -> httpx.Response:
    headers = dict(kwargs.pop("headers", {}) or {})
    headers["Authorization"] = f"Bearer {token}"
    return await provider_request(
        method,
        graph_url(path),
        provider_name="Microsoft Graph",
        client=client,
        headers=headers,
        **kwargs,
    )
