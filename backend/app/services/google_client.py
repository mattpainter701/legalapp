"""Google API helpers built on the shared provider HTTP layer."""

from __future__ import annotations

from typing import Any

import httpx

from app.services.provider_http import provider_request

GOOGLE_GMAIL_BASE_URL = "https://gmail.googleapis.com/gmail/v1"


def google_url(path: str, *, base_url: str = GOOGLE_GMAIL_BASE_URL) -> str:
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


async def google_request(
    method: str,
    path: str,
    *,
    token: str,
    client: httpx.AsyncClient | None = None,
    base_url: str = GOOGLE_GMAIL_BASE_URL,
    provider_name: str = "Google API",
    **kwargs: Any,
) -> httpx.Response:
    headers = dict(kwargs.pop("headers", {}) or {})
    headers["Authorization"] = f"Bearer {token}"
    return await provider_request(
        method,
        google_url(path, base_url=base_url),
        provider_name=provider_name,
        client=client,
        headers=headers,
        **kwargs,
    )


async def gmail_request(
    method: str,
    path: str,
    *,
    token: str,
    client: httpx.AsyncClient | None = None,
    **kwargs: Any,
) -> httpx.Response:
    return await google_request(
        method,
        path,
        token=token,
        client=client,
        base_url=GOOGLE_GMAIL_BASE_URL,
        provider_name="Gmail API",
        **kwargs,
    )
