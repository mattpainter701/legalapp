import uuid

import pytest

from app.models.contact import Contact
from app.schemas.matter_party import (
    MatterPartyCreate,
    MatterPartyUpdate,
    matter_party_role_definitions,
)


def test_matter_party_roles_are_canonical_and_documented():
    matter_id = uuid.uuid4()
    contact_id = uuid.uuid4()

    created = MatterPartyCreate(
        matter_id=matter_id,
        contact_id=contact_id,
        role="Plaintiffs",
    )
    updated = MatterPartyUpdate(role="Opposing Party")
    definitions = {
        definition.value: definition for definition in matter_party_role_definitions()
    }

    assert created.role == "plaintiff"
    assert updated.role == "opposing_party"
    assert definitions["plaintiff"].template_fields == [
        "plaintiff_name",
        "plaintiff_names",
    ]
    assert definitions["defendant"].template_fields == [
        "defendant_name",
        "defendant_names",
    ]
    assert "does not by itself identify" in definitions["client"].description


@pytest.mark.asyncio
async def test_matter_parties_endpoint_exposes_caption_roles(
    client,
    db_session,
    test_tenant,
):
    matter_response = await client.post(
        "/api/matters",
        json={"matter_name": "Smith v. Acme"},
    )
    assert matter_response.status_code == 201, matter_response.text
    matter_id = matter_response.json()["id"]

    contact = Contact(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        first_name="Jordan",
        last_name="Smith",
        email="jordan@example.com",
    )
    db_session.add(contact)
    await db_session.commit()

    added = await client.post(
        f"/api/matters/{matter_id}/parties",
        json={
            "matter_id": matter_id,
            "contact_id": str(contact.id),
            "role": "Plaintiffs",
            "is_primary": True,
        },
    )
    assert added.status_code == 201, added.text
    assert added.json()["role"] == "plaintiff"

    later_primary = Contact(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        entity_type="organization",
        organization_name="Acme Holdings",
    )
    db_session.add(later_primary)
    await db_session.commit()
    replacement = await client.post(
        f"/api/matters/{matter_id}/parties",
        json={
            "matter_id": matter_id,
            "contact_id": str(later_primary.id),
            "role": "plaintiff",
            "is_primary": True,
        },
    )
    assert replacement.status_code == 201, replacement.text

    listed = await client.get(f"/api/matters/{matter_id}/parties")
    assert listed.status_code == 200, listed.text
    payload = listed.json()
    definitions = {
        definition["value"]: definition for definition in payload["role_definitions"]
    }
    assert payload["items"][0]["contact_display_name"] == "Acme Holdings"
    assert [
        item["contact_display_name"] for item in payload["items"] if item["is_primary"]
    ] == ["Acme Holdings"]
    assert definitions["plaintiff"]["is_caption_role"] is True
    assert definitions["defendant"]["template_fields"] == [
        "defendant_name",
        "defendant_names",
    ]


@pytest.mark.asyncio
async def test_matter_party_body_matter_id_must_match_route(
    client,
    db_session,
    test_tenant,
):
    matter_response = await client.post(
        "/api/matters",
        json={"matter_name": "Route-bound party"},
    )
    matter_id = matter_response.json()["id"]
    contact = Contact(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        first_name="Route",
        last_name="Bound",
    )
    db_session.add(contact)
    await db_session.commit()

    response = await client.post(
        f"/api/matters/{matter_id}/parties",
        json={
            "matter_id": str(uuid.uuid4()),
            "contact_id": str(contact.id),
            "role": "defendant",
        },
    )

    assert response.status_code == 422
    assert "must match the route" in response.json()["detail"]
