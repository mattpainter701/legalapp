"""Bounded, content-free availability checks for active customer LLM routes.

The deployment gate runs this module inside the backend container. It resolves
the aliases customers actually use from the platform database, then makes one
synthetic completion per tier. Output contains route/status metadata only;
provider response text and credentials are never logged.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Mapping

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import async_session_maker
from app.services.llm_routing import get_platform_llm_config


settings = get_settings()
CUSTOMER_LLM_TIERS = ("standard", "premium")
CUSTOMER_LLM_PROBE_MAX_TOKENS = 512
CUSTOMER_LLM_PROBE_TIMEOUT_SECONDS = 45.0


def _visible_content(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    choices = payload.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return False
    message = choices[0].get("message") or {}
    return isinstance(message, dict) and bool(str(message.get("content") or "").strip())


async def probe_customer_llm_routes(
    aliases: Mapping[str, str],
    *,
    client: httpx.AsyncClient | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Probe every customer tier and return sanitized structured evidence."""

    gateway_url = (base_url or settings.LITELLM_BASE_URL or "").rstrip("/")
    gateway_key = api_key if api_key is not None else settings.LITELLM_API_KEY
    results: dict[str, dict[str, Any]] = {}

    if not gateway_url or not gateway_key:
        for tier in CUSTOMER_LLM_TIERS:
            results[tier] = {
                "alias": str(aliases.get(tier) or ""),
                "ok": False,
                "error_type": "gateway_configuration_missing",
            }
        return {"ok": False, "routes": results}

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=CUSTOMER_LLM_PROBE_TIMEOUT_SECONDS)

    try:
        for tier in CUSTOMER_LLM_TIERS:
            alias = str(aliases.get(tier) or "").strip()
            if not alias:
                results[tier] = {
                    "alias": "",
                    "ok": False,
                    "error_type": "active_alias_missing",
                }
                continue

            started = time.monotonic()
            try:
                response = await client.post(
                    f"{gateway_url}/chat/completions",
                    headers={"Authorization": f"Bearer {gateway_key}"},
                    json={
                        "model": alias,
                        "messages": [
                            {
                                "role": "system",
                                "content": "Follow the requested output format exactly.",
                            },
                            {
                                "role": "user",
                                "content": "Reply with exactly LAWHAND_READY",
                            },
                        ],
                        "temperature": 0,
                        "max_tokens": CUSTOMER_LLM_PROBE_MAX_TOKENS,
                    },
                )
                latency_ms = int((time.monotonic() - started) * 1000)
                visible = False
                if response.status_code == 200:
                    try:
                        visible = _visible_content(response.json())
                    except (TypeError, ValueError):
                        visible = False
                results[tier] = {
                    "alias": alias,
                    "ok": response.status_code == 200 and visible,
                    "status_code": response.status_code,
                    "visible_content": visible,
                    "latency_ms": latency_ms,
                }
                if response.status_code == 200 and not visible:
                    results[tier]["error_type"] = "visible_content_missing"
                elif response.status_code != 200:
                    results[tier]["error_type"] = "gateway_http_error"
            except Exception as exc:
                results[tier] = {
                    "alias": alias,
                    "ok": False,
                    "error_type": type(exc).__name__,
                    "latency_ms": int((time.monotonic() - started) * 1000),
                }
    finally:
        if owns_client:
            await client.aclose()

    return {
        "ok": all(results.get(tier, {}).get("ok") for tier in CUSTOMER_LLM_TIERS),
        "routes": results,
    }


async def probe_active_customer_llm_routes(db: AsyncSession) -> dict[str, Any]:
    config = await get_platform_llm_config(db)
    return await probe_customer_llm_routes(
        {
            "standard": str(config.get("standard_model") or ""),
            "premium": str(config.get("premium_model") or ""),
        }
    )


async def _run_cli() -> int:
    try:
        async with async_session_maker() as db:
            result = await probe_active_customer_llm_routes(db)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "check": "active_customer_llm_routes",
                    "ok": False,
                    "error_type": type(exc).__name__,
                }
            )
        )
        return 1
    print(json.dumps({"check": "active_customer_llm_routes", **result}, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run_cli()))
