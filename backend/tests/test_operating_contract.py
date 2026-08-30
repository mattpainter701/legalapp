"""Truth-boundary tests for the customer operating contract."""

import pytest

import json

from app.services.operating_contract import (
    assurance_program,
    operating_contract,
    security_review_packet,
    subprocessor_registry,
    support_policy,
    validate_operating_contract,
)


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


def test_support_subprocessors_and_assurance_are_specific_without_attainment_claims():
    policy = support_policy()
    assert "America/Chicago" in policy["coverage"]["standard_hours"]
    assert [item["severity"] for item in policy["severities"]] == [
        "S1", "S2", "S3", "S4",
    ]
    assert all(item["acknowledgement_objective_minutes"] > 0 for item in policy["severities"])

    registry = subprocessor_registry()
    assert registry["version"]
    assert {"IONOS", "Cloudflare", "Microsoft", "Google", "OpenAI", "Stripe"} <= {
        item["name"] for item in registry["entries"]
    }
    required = {"purpose", "data_categories", "region", "terms_state", "dpa_state", "baa_state"}
    assert all(required <= set(item) for item in registry["entries"])

    assurance = assurance_program()
    assert assurance["penetration_testing"]["evidence_state"] == "planned-not-attained"
    assert assurance["penetration_testing"]["latest_completed_evidence"] is None
    assert not any(item["attained"] for item in assurance["certification_roadmap"])


def test_security_review_packet_is_deterministic_exportable_and_public_safe():
    first = security_review_packet()
    second = security_review_packet()
    assert first == second
    assert len(first["sha256"]) == 64
    serialized = json.dumps(first).lower()
    for forbidden in ("f:\\", "/srv/", ".github/", "backend/app/", "password="):
        assert forbidden not in serialized


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

    packet = await client.get("/api/public/security-review-packet")
    assert packet.status_code == 200
    assert "attachment" in packet.headers["content-disposition"]
    assert packet.json()["schema"] == "lawhand.security-review-packet"
