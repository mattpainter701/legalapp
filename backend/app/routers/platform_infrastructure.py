"""Sanitized multi-site health for the operator-only Platform console."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.services.platform_auth import require_platform_token

router = APIRouter(prefix="/platform/infrastructure", tags=["platform"])


class InfrastructureTarget(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,31}$")
    label: str = Field(min_length=1, max_length=80)
    role: Literal["primary", "development", "disaster-recovery", "research"]
    url: str
    expected_release: str | None = None
    max_age_seconds: int | None = Field(default=None, ge=60, le=604800)


class ServiceStatus(BaseModel):
    id: str
    label: str
    role: str
    status: Literal["healthy", "degraded", "unavailable", "unconfigured"]
    checked_at: datetime
    release_sha: str | None = None
    writer_enabled: bool | None = None
    detail: str


class InfrastructureAlert(BaseModel):
    severity: Literal["warning", "critical"]
    service_id: str
    summary: str


class InfrastructureOverview(BaseModel):
    status: Literal["healthy", "degraded", "unconfigured"]
    checked_at: datetime
    services: list[ServiceStatus]
    alerts: list[InfrastructureAlert]


def _require_platform_read(request: Request) -> Any:
    return require_platform_token(request, scopes={"platform:read"})


def _target_is_allowed(target: InfrastructureTarget) -> bool:
    parsed = urlsplit(target.url)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        return False
    if parsed.scheme == "https" and parsed.hostname:
        return True
    if parsed.scheme != "http" or not parsed.hostname:
        return False
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        return parsed.hostname == "localhost"
    return address.is_loopback or address in ipaddress.ip_network("100.64.0.0/10")


def _load_targets() -> list[InfrastructureTarget]:
    raw = os.getenv("PLATFORM_INFRASTRUCTURE_TARGETS_JSON", "[]")
    try:
        value = json.loads(raw)
        if not isinstance(value, list) or len(value) > 12:
            return []
        targets = [InfrastructureTarget.model_validate(item) for item in value]
    except (json.JSONDecodeError, ValueError, TypeError):
        return []
    return targets if all(_target_is_allowed(target) for target in targets) else []


async def _probe(
    client: httpx.AsyncClient, target: InfrastructureTarget
) -> ServiceStatus:
    checked_at = datetime.now(timezone.utc)
    try:
        response = await client.get(target.url)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("health payload must be an object")
        reported_value = payload.get("status")
        reported = str(reported_value).lower() if reported_value is not None else None
        status = (
            "healthy"
            if reported is None or reported in {"ok", "healthy"}
            else "degraded"
        )
        release_sha = payload.get("release_sha") or payload.get("commit")
        if not isinstance(release_sha, str) or len(release_sha) > 64:
            release_sha = None
        writer_enabled = payload.get("writer_enabled")
        if not isinstance(writer_enabled, bool):
            writer_enabled = None
        detail = (
            "Health probe passed"
            if status == "healthy"
            else "Service reported a degraded state"
        )
        if target.max_age_seconds:
            source_checked_at = payload.get("checked_at")
            try:
                source_time = datetime.fromisoformat(
                    str(source_checked_at).replace("Z", "+00:00")
                ).astimezone(timezone.utc)
                age_seconds = (checked_at - source_time).total_seconds()
            except (TypeError, ValueError):
                age_seconds = target.max_age_seconds + 1
            if age_seconds < -300 or age_seconds > target.max_age_seconds:
                status = "degraded"
                detail = "Last successful source check is stale or invalid"
        if (
            target.expected_release
            and release_sha
            and release_sha != target.expected_release
        ):
            status = "degraded"
            detail = "Running release differs from the expected release"
        if target.role == "disaster-recovery" and writer_enabled is True:
            status = "degraded"
            detail = "DR writer is enabled while the primary is designated active"
        return ServiceStatus(
            id=target.id,
            label=target.label,
            role=target.role,
            status=status,
            checked_at=checked_at,
            release_sha=release_sha,
            writer_enabled=writer_enabled,
            detail=detail,
        )
    except (httpx.HTTPError, ValueError, TypeError):
        return ServiceStatus(
            id=target.id,
            label=target.label,
            role=target.role,
            status="unavailable",
            checked_at=checked_at,
            detail="Health probe did not complete",
        )


@router.get("", response_model=InfrastructureOverview)
async def infrastructure_overview(
    _auth: Any = Depends(_require_platform_read),
) -> InfrastructureOverview:
    targets = _load_targets()
    checked_at = datetime.now(timezone.utc)
    if not targets:
        return InfrastructureOverview(
            status="unconfigured", checked_at=checked_at, services=[], alerts=[]
        )

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(5.0, connect=3.0), follow_redirects=False
    ) as client:
        services = list(
            await asyncio.gather(*(_probe(client, target) for target in targets))
        )

    alerts: list[InfrastructureAlert] = []
    for service in services:
        if service.status == "unavailable":
            alerts.append(
                InfrastructureAlert(
                    severity="critical" if service.role == "primary" else "warning",
                    service_id=service.id,
                    summary=f"{service.label} is unavailable",
                )
            )
        elif service.status == "degraded":
            alerts.append(
                InfrastructureAlert(
                    severity="warning",
                    service_id=service.id,
                    summary=f"{service.label} is degraded",
                )
            )
    return InfrastructureOverview(
        status="healthy" if not alerts else "degraded",
        checked_at=checked_at,
        services=services,
        alerts=alerts,
    )
