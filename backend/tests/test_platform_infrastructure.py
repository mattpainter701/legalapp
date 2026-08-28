import json

import httpx
import pytest

from app.routers import platform_infrastructure as module


def test_targets_reject_public_plain_http(monkeypatch):
    monkeypatch.setenv(
        "PLATFORM_INFRASTRUCTURE_TARGETS_JSON",
        json.dumps(
            [
                {
                    "id": "bad",
                    "label": "Bad",
                    "role": "development",
                    "url": "http://example.com/health",
                }
            ]
        ),
    )
    assert module._load_targets() == []


def test_targets_allow_https_and_tailscale(monkeypatch):
    monkeypatch.setenv(
        "PLATFORM_INFRASTRUCTURE_TARGETS_JSON",
        json.dumps(
            [
                {
                    "id": "dev1",
                    "label": "Dev 1",
                    "role": "development",
                    "url": "https://dev1.getlawhand.com/health/readiness",
                },
                {
                    "id": "dr",
                    "label": "Skynet DR",
                    "role": "disaster-recovery",
                    "url": "http://100.108.171.10:19090/status",
                },
            ]
        ),
    )
    assert [target.id for target in module._load_targets()] == ["dev1", "dr"]


@pytest.mark.asyncio
async def test_probe_flags_enabled_dr_writer():
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "healthy", "writer_enabled": True})

    target = module.InfrastructureTarget(
        id="dr",
        label="Skynet DR",
        role="disaster-recovery",
        url="http://100.108.171.10:19090/status",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await module._probe(client, target)
    assert result.status == "degraded"
    assert result.writer_enabled is True


@pytest.mark.asyncio
async def test_probe_hides_transport_error_details():
    async def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("secret internal detail")

    target = module.InfrastructureTarget(
        id="dev1",
        label="Dev 1",
        role="development",
        url="https://dev1.getlawhand.com/health/readiness",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await module._probe(client, target)
    assert result.status == "unavailable"
    assert "secret" not in result.detail


def test_targets_fail_closed_for_invalid_or_credentialed_configuration(monkeypatch):
    monkeypatch.setenv("PLATFORM_INFRASTRUCTURE_TARGETS_JSON", "not-json")
    assert module._load_targets() == []

    monkeypatch.setenv(
        "PLATFORM_INFRASTRUCTURE_TARGETS_JSON",
        json.dumps(
            [
                {
                    "id": "bad",
                    "label": "Credentialed URL",
                    "role": "development",
                    "url": "https://user:password@example.com/health",
                }
            ]
        ),
    )
    assert module._load_targets() == []


@pytest.mark.asyncio
async def test_probe_flags_stale_release_and_sanitizes_invalid_payload():
    async def stale_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "healthy",
                "commit": "old-release",
                "checked_at": "not-a-timestamp",
            },
        )

    target = module.InfrastructureTarget(
        id="dev1",
        label="Dev 1",
        role="development",
        url="https://dev1.getlawhand.com/health/readiness",
        expected_release="expected-release",
        max_age_seconds=300,
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(stale_handler)
    ) as client:
        result = await module._probe(client, target)
    assert result.status == "degraded"
    assert result.release_sha == "old-release"
    assert result.detail == "Running release differs from the expected release"

    async def invalid_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(invalid_handler)
    ) as client:
        result = await module._probe(client, target)
    assert result.status == "unavailable"
    assert result.detail == "Health probe did not complete"


@pytest.mark.asyncio
async def test_infrastructure_overview_reports_unconfigured(monkeypatch):
    monkeypatch.setattr(module, "_load_targets", lambda: [])
    result = await module.infrastructure_overview()
    assert result.status == "unconfigured"
    assert result.services == []
    assert result.alerts == []


@pytest.mark.asyncio
async def test_infrastructure_overview_prioritizes_primary_outage(monkeypatch):
    targets = [
        module.InfrastructureTarget(
            id="primary",
            label="IONOS primary",
            role="primary",
            url="https://getlawhand.com/health/readiness",
        ),
        module.InfrastructureTarget(
            id="dev1",
            label="Dev 1",
            role="development",
            url="https://dev1.getlawhand.com/health/readiness",
        ),
    ]
    monkeypatch.setattr(module, "_load_targets", lambda: targets)

    async def fake_probe(_client, target):
        return module.ServiceStatus(
            id=target.id,
            label=target.label,
            role=target.role,
            status="unavailable" if target.role == "primary" else "degraded",
            checked_at=module.datetime.now(module.timezone.utc),
            detail="synthetic test state",
        )

    monkeypatch.setattr(module, "_probe", fake_probe)
    result = await module.infrastructure_overview()
    assert result.status == "degraded"
    assert [(alert.service_id, alert.severity) for alert in result.alerts] == [
        ("primary", "critical"),
        ("dev1", "warning"),
    ]
