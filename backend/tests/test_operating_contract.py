"""Truth-boundary tests for the customer operating contract."""

import pytest

from app.services.operating_contract import operating_contract, validate_operating_contract


def test_operating_contract_is_complete_and_truthful():
    contract = operating_contract()
    assert validate_operating_contract(contract) == []
    ids = {control["id"] for control in contract["controls"]}
    assert {
        "topology", "service-objectives", "support", "status-incidents",
        "backup-restore", "tenant-export", "onboarding-migration",
        "offboarding-deletion", "privacy-terms", "subprocessors",
        "security-review", "penetration-testing", "certification-roadmap",
    } <= ids
    assert any(control["status"] == "planned" for control in contract["controls"])


def test_contract_rejects_unbounded_claims():
    contract = operating_contract()
    contract["controls"][0]["boundary"] = ""
    assert "claim boundary missing for topology" in validate_operating_contract(contract)


@pytest.mark.asyncio
async def test_public_contract_endpoint_is_unauthenticated(client):
    response = await client.get("/api/public/operating-contract")
    assert response.status_code == 200
    payload = response.json()
    assert payload["schema"] == "lawhand.operating-contract"
    assert payload["version"]
    assert any(item["id"] == "certification-roadmap" and item["status"] == "planned" for item in payload["controls"])
