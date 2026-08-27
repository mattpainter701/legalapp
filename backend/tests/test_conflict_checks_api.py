"""Saved conflict-check workflow regression coverage."""

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.models.contact import Contact
from app.models.matter_party import MatterParty
from app.models.plugin import Matter
from app.routers.conflict_checks import ZERO_UUID, _snapshot_matches
from app.services.conflict_report_pdf import generate_conflict_report_pdf


@pytest.mark.asyncio
async def test_conflict_search_is_saved_reviewed_and_locked(
    client, db_session, test_tenant, test_user
):
    contact_id = uuid.uuid4()
    matter_id = uuid.uuid4()
    db_session.add(
        Contact(
            id=contact_id,
            tenant_id=test_tenant.id,
            entity_type="person",
            contact_type="opposing_party",
            first_name="Alice",
            last_name="Smith",
            email="alice@example.com",
            is_active=True,
        )
    )
    db_session.add(
        Matter(
            id=matter_id,
            tenant_id=test_tenant.id,
            user_id=test_user.id,
            slug=f"smith-party-{matter_id.hex[:8]}",
            matter_name="Acme v. Smith",
            matter_type="litigation",
        )
    )
    db_session.add(
        MatterParty(
            id=uuid.uuid4(),
            tenant_id=test_tenant.id,
            matter_id=matter_id,
            contact_id=contact_id,
            role="witness",
        )
    )
    await db_session.commit()

    created = await client.post(
        "/api/conflict-checks",
        json={
            "label": "Smith intake",
            "names": ["Alice Smith", " Alice Smith "],
            "emails": ["alice@example.com"],
            "organization_names": [],
        },
    )
    assert created.status_code == 201, created.text
    record = created.json()
    assert record["status"] == "open"
    assert record["decision"] == "needs_review"
    assert record["query"]["names"] == ["Alice Smith"]
    assert record["match_count"] == 1
    assert record["matches"][0]["display_name"] == "Alice Smith"
    assert record["matches"][0]["matter_names"] == ["Acme v. Smith"]

    record_id = record["id"]
    unacknowledged = await client.post(
        f"/api/conflict-checks/{record_id}/close",
        json={
            "decision": "no_conflict_found",
            "notes": "Reviewed the matched contact and representation history.",
            "acknowledge_attorney_review": False,
        },
    )
    assert unacknowledged.status_code == 422

    closed = await client.post(
        f"/api/conflict-checks/{record_id}/close",
        json={
            "decision": "no_conflict_found",
            "notes": "Reviewed the matched contact and representation history.",
            "acknowledge_attorney_review": True,
        },
    )
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "closed"
    assert closed.json()["closed_at"] is not None

    immutable = await client.post(
        f"/api/conflict-checks/{record_id}/close",
        json={
            "decision": "conflict_found",
            "notes": "Attempted later change.",
            "acknowledge_attorney_review": True,
        },
    )
    assert immutable.status_code == 409

    report = await client.get(f"/api/conflict-checks/{record_id}/report.pdf")
    assert report.status_code == 200
    assert report.headers["content-type"].startswith("application/pdf")
    assert report.content.startswith(b"%PDF")


def test_restricted_counterparty_match_preserves_warning_without_details():
    matter_id = uuid.uuid4()
    snapshot, restricted = _snapshot_matches(
        [
            {
                "contact_id": ZERO_UUID,
                "display_name": "Hidden Counterparty LLC",
                "contact_type": "opposing_party",
                "email": None,
                "match_field": "matter_counterparty",
                "match_value": "Hidden Counterparty",
                "matter_ids": [matter_id],
                "matter_names": ["Restricted Matter"],
            }
        ],
        visible=set(),
    )
    assert restricted == 1
    assert snapshot[0]["display_name"] == "Restricted potential match"
    assert snapshot[0]["matter_ids"] == []
    assert snapshot[0]["matter_names"] == []
    assert snapshot[0]["restricted_matter_count"] == 1


def test_saved_snapshot_renders_as_pdf_without_a_database():
    record = SimpleNamespace(
        label="Smith intake",
        status="closed",
        decision="no_conflict_found",
        query_snapshot={"names": ["Alice Smith"], "organization_names": [], "emails": []},
        result_snapshot=[
            {
                "display_name": "Alice Smith",
                "contact_type": "opposing_party",
                "match_field": "name",
                "match_value": "Alice Smith",
                "matter_names": ["Acme v. Smith"],
                "restricted_matter_count": 0,
            }
        ],
        match_count=1,
        restricted_matter_count=0,
        notes="Reviewed representation history.",
        created_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
        closed_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
    )

    report = generate_conflict_report_pdf(record)

    assert report.startswith(b"%PDF")
    assert len(report) > 1_000
