"""Tests for the legal practice plugin system."""

from datetime import date, timedelta

import pytest
from httpx import AsyncClient

COMPLETE_PROFILE = """# Commercial Legal Practice Profile
## Liability Cap Position (Sales): 12 months of fees
## Liability Cap Position (Buying): 2x annual fees, IP carveout unlimited
## Indemnification: Mutual, each party indemnifies own IP
## Data Protection Standard: GDPR-level SCCs
## Governing Law: Delaware
## Term Default: 12 months auto-renew
## Deal-Breaker: Unlimited liability without cap"""


@pytest.mark.asyncio
async def test_list_plugins(client: AsyncClient):
    resp = await client.get("/api/plugins")
    assert resp.status_code == 200
    data = resp.json()
    assert "plugins" in data
    names = [p["name"] for p in data["plugins"]]
    for expected in [
        "commercial-legal", "litigation-legal", "privacy-legal",
        "corporate-legal", "employment-legal", "product-legal",
        "ip-legal", "ai-governance-legal", "regulatory-legal",
    ]:
        assert expected in names


@pytest.mark.asyncio
async def test_profile_empty_by_default(client: AsyncClient):
    resp = await client.get("/api/plugins/employment-legal/profile")
    assert resp.status_code == 200
    assert resp.json()["is_complete"] is False


@pytest.mark.asyncio
async def test_save_and_retrieve_profile(client: AsyncClient):
    resp = await client.put(
        "/api/plugins/commercial-legal/profile",
        json={"profile_content": COMPLETE_PROFILE, "is_complete": True},
    )
    assert resp.status_code == 200
    assert resp.json()["is_complete"] is True

    get_resp = await client.get("/api/plugins/commercial-legal/profile")
    assert get_resp.json()["profile_content"] == COMPLETE_PROFILE


@pytest.mark.asyncio
async def test_skill_gate_no_profile(client: AsyncClient, mock_llm):
    resp = await client.post(
        "/api/plugins/ip-legal/trademark-clearance",
        json={"skill": "trademark-clearance", "input_text": "Proposed mark: LEXAI"},
    )
    assert resp.status_code == 200
    assert len(resp.json()["gates_triggered"]) > 0


@pytest.mark.asyncio
async def test_skill_executes_with_profile(client: AsyncClient, mock_llm):
    await client.put(
        "/api/plugins/commercial-legal/profile",
        json={"profile_content": COMPLETE_PROFILE, "is_complete": True},
    )
    resp = await client.post(
        "/api/plugins/commercial-legal/nda-review",
        json={
            "skill": "nda-review",
            "input_text": "NON-DISCLOSURE AGREEMENT between Party A and Party B...",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["requires_attorney_review"] is True
    assert len(data["memo"]) > 10


@pytest.mark.asyncio
async def test_matter_create_and_retrieve(client: AsyncClient):
    resp = await client.post(
        "/api/plugins/litigation/matters",
        json={
            "matter_name": "Acme Corp v. Widget LLC",
            "matter_type": "contract",
            "counterparty": "Widget LLC",
            "jurisdiction": "S.D.N.Y.",
            "role": "plaintiff",
            "source": "Demand letter received",
        },
    )
    assert resp.status_code == 201
    matter = resp.json()
    assert matter["conflicts_status"] == "not-run"
    matter_id = matter["id"]

    list_resp = await client.get("/api/plugins/litigation/matters")
    assert list_resp.status_code == 200
    assert any(m["id"] == matter_id for m in list_resp.json())

    detail_resp = await client.get(f"/api/plugins/litigation/matters/{matter_id}")
    assert detail_resp.status_code == 200


@pytest.mark.asyncio
async def test_matter_event_append(client: AsyncClient):
    matter = (
        await client.post(
            "/api/plugins/litigation/matters",
            json={
                "matter_name": "Event Test Matter",
                "matter_type": "ip",
                "counterparty": "ACME",
                "jurisdiction": "N.D. Cal.",
                "role": "defendant",
                "source": "Complaint served",
            },
        )
    ).json()

    event_resp = await client.post(
        f"/api/plugins/litigation/matters/{matter['id']}/events",
        json={
            "event_type": "update",
            "title": "Initial assessment complete",
            "content": "Reviewed complaint. Defense strategy: invalidity.",
        },
    )
    assert event_resp.status_code == 201


@pytest.mark.asyncio
async def test_renewal_urgency_critical(client: AsyncClient):
    renewal_date = (date.today() + timedelta(days=8)).isoformat()
    resp = await client.post(
        "/api/plugins/commercial/renewals",
        json={
            "contract_name": "Critical SaaS",
            "vendor": "Acme Inc",
            "renewal_date": renewal_date,
            "notice_deadline": (date.today() + timedelta(days=2)).isoformat(),
            "contract_value_annual": 100000,
            "auto_renewal": True,
        },
    )
    assert resp.status_code == 201
    assert resp.json()["urgency"] == "critical"


@pytest.mark.asyncio
async def test_renewal_urgency_low(client: AsyncClient):
    renewal_date = (date.today() + timedelta(days=80)).isoformat()
    resp = await client.post(
        "/api/plugins/commercial/renewals",
        json={
            "contract_name": "Low Urgency Contract",
            "vendor": "ACME",
            "renewal_date": renewal_date,
            "auto_renewal": False,
        },
    )
    assert resp.status_code == 201
    assert resp.json()["urgency"] == "low"
