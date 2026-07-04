"""Shared HTTP discipline for external provider APIs."""

from __future__ import annotations

import asyncio
import email.utils
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

import httpx

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_RETRIES = 2
DEFAULT_BACKOFF_SECONDS = 0.5
MAX_RETRY_AFTER_SECONDS = 30.0


class ProviderError(RuntimeError):
    """Base exception for provider HTTP failures."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
        response_text: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after
        self.response_text = response_text


class ProviderAuthError(ProviderError):
    """Provider rejected credentials or permission scope."""


class ProviderThrottled(ProviderError):
    """Provider throttled the request after bounded retry attempts."""


class ProviderNotFound(ProviderError):
    """Provider resource was not found."""


def _response_preview(response: httpx.Response) -> str:
    try:
        return response.text[:500]
    except Exception:
        return ""


def _retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        seconds = float(value)
        return max(0.0, seconds)
    except ValueError:
        pass

    try:
        retry_at = email.utils.parsedate_to_datetime(value)
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
    except Exception:
        return None


def _backoff(attempt: int, retry_after: float | None) -> float:
    if retry_after is not None:
        return min(retry_after, MAX_RETRY_AFTER_SECONDS)
    return min(DEFAULT_BACKOFF_SECONDS * (2**attempt), MAX_RETRY_AFTER_SECONDS)


def _raise_for_provider_response(response: httpx.Response, provider_name: str) -> None:
    status_code = response.status_code
    if status_code < 400:
        return

    retry_after = _retry_after_seconds(response.headers.get("Retry-After"))
    preview = _response_preview(response)
    message = f"{provider_name} request failed with HTTP {status_code}"
    kwargs = {
        "status_code": status_code,
        "retry_after": retry_after,
        "response_text": preview,
    }

    if status_code in (401, 403):
        raise ProviderAuthError(message, **kwargs)
    if status_code == 404:
        raise ProviderNotFound(message, **kwargs)
    if status_code == 429:
        raise ProviderThrottled(f"{provider_name} request was throttled", **kwargs)
    raise ProviderError(message, **kwargs)


async def provider_request(
    method: str,
    url: str,
    *,
    provider_name: str = "Provider",
    client: httpx.AsyncClient | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_retries: int = DEFAULT_MAX_RETRIES,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    **kwargs: Any,
) -> httpx.Response:
    """Send a provider request with timeout, throttling, and transient retries."""

    owns_client = client is None
    active_client = client or httpx.AsyncClient(timeout=timeout)

    try:
        for attempt in range(max_retries + 1):
            try:
                response = await active_client.request(method, url, **kwargs)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt < max_retries:
                    await sleep(_backoff(attempt, None))
                    continue
                raise ProviderError(
                    f"{provider_name} request failed before response: {exc}"
                ) from exc

            status_code = response.status_code
            if status_code == 429 or 500 <= status_code < 600:
                retry_after = _retry_after_seconds(response.headers.get("Retry-After"))
                if attempt < max_retries:
                    await sleep(_backoff(attempt, retry_after))
                    continue

            _raise_for_provider_response(response, provider_name)
            return response
    finally:
        if owns_client:
            await active_client.aclose()

    raise ProviderError(f"{provider_name} request failed")
