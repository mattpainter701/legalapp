from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlparse

import httpx

logger = logging.getLogger("clarity_agent.api")

_MAX_RETRIES = 3
_BASE_DELAY = 1.0


class SaaSClient:
    def __init__(self, config):
        self.base_url = config.saas_url.rstrip("/")
        if any(
            character == "\\" or ord(character) < 32 or ord(character) == 127
            for character in self.base_url
        ):
            raise ValueError("CLARITY_SAAS_URL contains invalid URL characters")
        parsed = urlparse(self.base_url)
        # The API key and per-share credentials traverse this channel. Never
        # permit an accidental plaintext production deployment; loopback HTTP
        # remains useful for local development and tests.
        try:
            hostname = parsed.hostname
            parsed.port
        except ValueError as exc:
            raise ValueError("CLARITY_SAAS_URL has a malformed host") from exc
        loopback_http = (
            parsed.scheme == "http"
            and hostname
            and hostname.casefold()
            in {
                "localhost",
                "127.0.0.1",
                "::1",
            }
        )
        if parsed.scheme != "https" and not loopback_http:
            raise ValueError("CLARITY_SAAS_URL must use HTTPS")
        if (
            not hostname
            or any(character.isspace() for character in hostname)
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "CLARITY_SAAS_URL must not contain credentials, query, or fragment"
            )
        self.api_key = config.api_key
        self.agent_id = config.agent_id
        self.http = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"X-Agent-API-Key": self.api_key},
            timeout=30.0,
        )

    async def close(self) -> None:
        await self.http.aclose()

    async def _request(self, method: str, url: str, **kwargs) -> dict:
        last_exc = None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                resp = await self.http.request(method, url, **kwargs)
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as exc:
                logger.warning(
                    "HTTP %s %s -> %d (attempt %d/%d)",
                    method,
                    url,
                    exc.response.status_code,
                    attempt,
                    _MAX_RETRIES,
                )
                last_exc = exc
                if exc.response.status_code < 500:
                    raise
            except httpx.RequestError as exc:
                logger.warning(
                    "Request error %s %s: %s (attempt %d/%d)",
                    method,
                    url,
                    exc,
                    attempt,
                    _MAX_RETRIES,
                )
                last_exc = exc
            delay = _BASE_DELAY * (2 ** (attempt - 1))
            await asyncio.sleep(delay)
        raise last_exc  # type: ignore[misc]

    async def register(self, pairing_code: str, agent_info: dict) -> dict:
        return await self._request(
            "POST",
            "/api/v1/smb/agents/register",
            json={"pairing_code": pairing_code, **agent_info},
        )

    async def sync(
        self, files: list[dict], deletions: list[str], share_id: str
    ) -> dict:
        return await self._request(
            "POST",
            f"/api/v1/smb/agents/{self.agent_id}/sync",
            params={"share_id": share_id},
            json={"files": files, "deletions": deletions},
        )

    async def get_tasks(self) -> list[dict]:
        result = await self._request(
            "GET",
            f"/api/v1/smb/agents/{self.agent_id}/tasks",
            params={"wait_seconds": 20},
        )
        return result if isinstance(result, list) else result.get("tasks", [])

    async def submit_task_result(
        self,
        task_id: str,
        content: str = "",
        truncated: bool = False,
        error: str | None = None,
        ok: bool | None = None,
        detail: dict | None = None,
    ) -> dict:
        """Report a task outcome.

        ``ok``/``detail`` carry the result of the operational tasks the admin
        console triggers (connection test, scan now); a content fetch leaves
        them unset.
        """
        payload: dict = {
            "task_id": task_id,
            "content": content,
            "truncated": truncated,
            "ok": (error is None) if ok is None else ok,
        }
        if error:
            payload["error"] = error
        if detail is not None:
            payload["detail"] = detail
        return await self._request(
            "POST",
            f"/api/v1/smb/agents/{self.agent_id}/tasks/{task_id}/result",
            json=payload,
        )

    async def report_scan_status(
        self,
        share_id: str,
        status: str,
        file_count: int | None = None,
        error: str | None = None,
        started_at: str | None = None,
        finished_at: str | None = None,
    ) -> dict:
        """Tell the SaaS how a share scan ended, so admins can see it."""
        payload: dict = {"status": status}
        if file_count is not None:
            payload["file_count"] = file_count
        if error:
            payload["error"] = error[:2000]
        if started_at:
            payload["started_at"] = started_at
        if finished_at:
            payload["finished_at"] = finished_at
        return await self._request(
            "POST",
            f"/api/v1/smb/agents/{self.agent_id}/shares/{share_id}/scan-status",
            json=payload,
        )

    async def heartbeat(self, data: dict) -> dict:
        return await self._request(
            "POST",
            f"/api/v1/smb/agents/{self.agent_id}/heartbeat",
            json=data,
        )

    async def get_shares(self) -> list[dict]:
        result = await self._request(
            "GET",
            f"/api/v1/smb/agents/{self.agent_id}/shares",
        )
        return result if isinstance(result, list) else result.get("shares", [])
