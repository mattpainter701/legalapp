"""Native client-portal mediation overlay confidentiality and entitlement tests."""

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from app.models.client_portal import ClientPortalInvite
from app.models.contact import Contact
from app.models.mediation import (
    MediationAsset,
    MediationDocument,
    MediationDocumentRecipient,
    MediationParty,
    MediationProposal,
    MediationProposalRecipient,
)
from app.models.plugin import MediationCase, Matter, TenantPluginEntitlement
from app.routers.client_portal import CLIENT_PORTAL_COOKIE_NAME
from app.services.portal_token import create_matter_portal_token


PORTAL = "/api/portal/client"


@pytest_asyncio.fixture
async def native_mediation_portal(db_session, test_tenant, test_user):
    """A native matter portal identity linked to one mediation case/party."""
    contact = Contact(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        contact_type="client",
        client_status="active",
        first_name="Jane",
        last_name="Doe",
        email="client@example.com",
    )
    matter = Matter(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        slug=f"mediation-overlay-{uuid.uuid4().hex[:8]}",
        matter_name="Doe v. Doe",
        matter_type="mediation",
        status="open",
        portal_enabled=True,
        client_contact_id=contact.id,
    )
    case = MediationCase(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        title="Doe v. Doe mediation",
        case_name="Doe v. Doe mediation",
        matter_id=matter.id,
        client_contact_id=contact.id,
    )
    party = MediationParty(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        case_id=case.id,
        role="our_client",
        name="Jane Doe",
        email="client@example.com",
    )
    # These models expose the foreign keys but do not declare every ORM
    # relationship, so make dependency order explicit for PostgreSQL.
    db_session.add(contact)
    await db_session.flush()
    db_session.add(matter)
    await db_session.flush()
    db_session.add(case)
    await db_session.flush()
    db_session.add(party)
    await db_session.flush()
    raw = secrets.token_urlsafe(32)
    invite = ClientPortalInvite(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        matter_id=matter.id,
        contact_id=contact.id,
        token_hash=hashlib.sha256(raw.encode()).hexdigest(),
        email=contact.email,
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    entitlement = TenantPluginEntitlement(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        plugin_name="mediation-legal",
        status="purchased",
    )
    db_session.add_all([invite, entitlement])
    await db_session.commit()
    token = create_matter_portal_token(
        tenant_id=str(test_tenant.id),
        matter_id=str(matter.id),
        contact_id=str(contact.id),
        email=contact.email,
        invite_id=str(invite.id),
    )
    return {
        "contact": contact,
        "matter": matter,
        "case": case,
        "party": party,
        "invite": invite,
        "entitlement": entitlement,
        "headers": {
            "Cookie": f"{CLIENT_PORTAL_COOKIE_NAME}={token}",
            "Authorization": "",
        },
    }


@pytest.mark.asyncio
async def test_native_mediation_overlay_requires_active_entitlement_and_matching_contact(
    client, db_session, native_mediation_portal
):
    fixture = native_mediation_portal
    response = await client.get(f"{PORTAL}/mediation", headers=fixture["headers"])
    assert response.status_code == 200, response.text
    assert response.json()["case"]["id"] == str(fixture["case"].id)

    await db_session.delete(fixture["entitlement"])
    await db_session.commit()
    response = await client.get(f"{PORTAL}/mediation", headers=fixture["headers"])
    assert response.status_code == 404
    fixture["entitlement"] = TenantPluginEntitlement(
        id=uuid.uuid4(),
        tenant_id=fixture["matter"].tenant_id,
        plugin_name="mediation-legal",
        status="purchased",
    )
    db_session.add(fixture["entitlement"])
    await db_session.commit()
    for status in ("locked", "disabled", "unlicensed", "expired"):
        fixture["entitlement"].status = status
        await db_session.commit()
        response = await client.get(f"{PORTAL}/mediation", headers=fixture["headers"])
        assert response.status_code == 404
    fixture["entitlement"].status = "trial"
    fixture["entitlement"].expires_at = None
    await db_session.commit()
    assert (
        await client.get(f"{PORTAL}/mediation", headers=fixture["headers"])
    ).status_code == 404
    fixture["entitlement"].expires_at = datetime.now(timezone.utc) - timedelta(
        seconds=1
    )
    await db_session.commit()
    assert (
        await client.get(f"{PORTAL}/mediation", headers=fixture["headers"])
    ).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["included", "trial"])
async def test_native_mediation_overlay_accepts_included_or_unexpired_trial(
    client, db_session, native_mediation_portal, status
):
    fixture = native_mediation_portal
    fixture["entitlement"].status = status
    fixture["entitlement"].expires_at = (
        datetime.now(timezone.utc) + timedelta(days=1) if status == "trial" else None
    )
    await db_session.commit()
    response = await client.get(f"{PORTAL}/mediation", headers=fixture["headers"])
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_native_mediation_overlay_matches_party_contact_then_email_fallback(
    client, db_session, native_mediation_portal
):
    fixture = native_mediation_portal
    fixture["party"].contact_id = fixture["contact"].id
    fixture["case"].client_contact_id = None
    await db_session.commit()
    direct = await client.get(f"{PORTAL}/mediation", headers=fixture["headers"])
    assert direct.status_code == 200, direct.text

    fixture["party"].role = "mediator"
    await db_session.commit()
    assert (
        await client.get(f"{PORTAL}/mediation", headers=fixture["headers"])
    ).status_code == 404
    fixture["party"].role = "our_client"

    fixture["party"].contact_id = None
    fixture["case"].client_contact_id = None
    fixture["invite"].contact_id = None
    await db_session.commit()
    email_token = create_matter_portal_token(
        tenant_id=str(fixture["matter"].tenant_id),
        matter_id=str(fixture["matter"].id),
        contact_id=None,
        email="CLIENT@example.com",
        invite_id=str(fixture["invite"].id),
    )
    email_headers = {
        "Cookie": f"{CLIENT_PORTAL_COOKIE_NAME}={email_token}",
        "Authorization": "",
    }
    matched = await client.get(f"{PORTAL}/mediation", headers=email_headers)
    assert matched.status_code == 200, matched.text

    fixture["party"].role = "attorney"
    await db_session.commit()
    assert (
        await client.get(f"{PORTAL}/mediation", headers=email_headers)
    ).status_code == 404
    fixture["party"].role = "our_client"
    await db_session.commit()

    mismatch_token = create_matter_portal_token(
        tenant_id=str(fixture["matter"].tenant_id),
        matter_id=str(fixture["matter"].id),
        contact_id=None,
        email="different@example.com",
        invite_id=str(fixture["invite"].id),
    )
    mismatch = await client.get(
        f"{PORTAL}/mediation",
        headers={
            "Cookie": f"{CLIENT_PORTAL_COOKIE_NAME}={mismatch_token}",
            "Authorization": "",
        },
    )
    assert mismatch.status_code == 404


@pytest.mark.asyncio
async def test_native_mediation_overlay_fails_closed_for_contact_and_case_ambiguity(
    client, db_session, native_mediation_portal
):
    fixture = native_mediation_portal
    fixture["case"].client_contact_id = None
    await db_session.commit()
    assert (
        await client.get(f"{PORTAL}/mediation", headers=fixture["headers"])
    ).status_code == 404

    # A token claiming another tenant cannot reuse this invite or case.
    forged = create_matter_portal_token(
        tenant_id=str(uuid.uuid4()),
        matter_id=str(fixture["matter"].id),
        contact_id=str(fixture["contact"].id),
        email=fixture["contact"].email,
        invite_id=str(fixture["invite"].id),
    )
    assert (
        await client.get(
            f"{PORTAL}/mediation",
            headers={
                "Cookie": f"{CLIENT_PORTAL_COOKIE_NAME}={forged}",
                "Authorization": "",
            },
        )
    ).status_code == 401

    fixture["case"].client_contact_id = fixture["contact"].id
    fixture["party"].contact_id = fixture["contact"].id
    duplicate_party = MediationParty(
        id=uuid.uuid4(),
        tenant_id=fixture["matter"].tenant_id,
        case_id=fixture["case"].id,
        role="our_client",
        name="Duplicate Jane",
        contact_id=fixture["contact"].id,
    )
    db_session.add(duplicate_party)
    await db_session.commit()
    assert (
        await client.get(f"{PORTAL}/mediation", headers=fixture["headers"])
    ).status_code == 404
    await db_session.delete(duplicate_party)
    await db_session.commit()

    second = MediationCase(
        id=uuid.uuid4(),
        tenant_id=fixture["matter"].tenant_id,
        title="Ambiguous second case",
        matter_id=fixture["matter"].id,
        client_contact_id=fixture["contact"].id,
    )
    db_session.add(second)
    await db_session.commit()
    assert (
        await client.get(f"{PORTAL}/mediation", headers=fixture["headers"])
    ).status_code == 409


@pytest.mark.asyncio
async def test_native_mediation_overlay_filters_records_and_recipient_ids(
    client, db_session, native_mediation_portal, tmp_path
):
    fixture = native_mediation_portal
    other_party = MediationParty(
        id=uuid.uuid4(),
        tenant_id=fixture["matter"].tenant_id,
        case_id=fixture["case"].id,
        role="opposing_party",
        name="John Doe",
    )
    third_contact = Contact(
        id=uuid.uuid4(),
        tenant_id=fixture["matter"].tenant_id,
        contact_type="client",
        client_status="active",
        first_name="Alex",
        last_name="Smith",
        email="alex@example.com",
    )
    third_party = MediationParty(
        id=uuid.uuid4(),
        tenant_id=fixture["matter"].tenant_id,
        case_id=fixture["case"].id,
        role="our_client",
        name="Alex Smith",
        contact_id=third_contact.id,
    )
    third_invite = ClientPortalInvite(
        id=uuid.uuid4(),
        tenant_id=fixture["matter"].tenant_id,
        matter_id=fixture["matter"].id,
        contact_id=third_contact.id,
        token_hash=hashlib.sha256(secrets.token_bytes(32)).hexdigest(),
        email=third_contact.email,
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    document = MediationDocument(
        id=uuid.uuid4(),
        tenant_id=fixture["matter"].tenant_id,
        case_id=fixture["case"].id,
        uploaded_by_party_id=fixture["party"].id,
        filename="my.pdf",
        storage_path=str(tmp_path / "my.pdf"),
        content_sha256=hashlib.sha256(b"my document").hexdigest(),
    )
    hidden_document = MediationDocument(
        id=uuid.uuid4(),
        tenant_id=fixture["matter"].tenant_id,
        case_id=fixture["case"].id,
        uploaded_by_party_id=other_party.id,
        filename="other.pdf",
        storage_path=str(tmp_path / "other.pdf"),
    )
    private_document = MediationDocument(
        id=uuid.uuid4(),
        tenant_id=fixture["matter"].tenant_id,
        case_id=fixture["case"].id,
        uploaded_by_party_id=other_party.id,
        filename="private.pdf",
        storage_path=str(tmp_path / "private.pdf"),
    )
    proposal = MediationProposal(
        id=uuid.uuid4(),
        tenant_id=fixture["matter"].tenant_id,
        case_id=fixture["case"].id,
        proposed_by_party_id=fixture["party"].id,
        title="My offer",
        body="Private terms",
    )
    hidden_proposal = MediationProposal(
        id=uuid.uuid4(),
        tenant_id=fixture["matter"].tenant_id,
        case_id=fixture["case"].id,
        proposed_by_party_id=other_party.id,
        title="Other offer",
        body="Other private terms",
    )
    own_asset = MediationAsset(
        id=uuid.uuid4(),
        tenant_id=fixture["matter"].tenant_id,
        case_id=fixture["case"].id,
        submitted_by_party_id=fixture["party"].id,
        description="My draft",
        kind="asset",
        status="draft",
    )
    private_asset = MediationAsset(
        id=uuid.uuid4(),
        tenant_id=fixture["matter"].tenant_id,
        case_id=fixture["case"].id,
        submitted_by_party_id=other_party.id,
        description="Other draft",
        kind="asset",
        status="draft",
    )
    shared_asset = MediationAsset(
        id=uuid.uuid4(),
        tenant_id=fixture["matter"].tenant_id,
        case_id=fixture["case"].id,
        submitted_by_party_id=other_party.id,
        description="Shared asset",
        kind="asset",
        status="sent",
    )
    (tmp_path / "my.pdf").write_bytes(b"my document")
    (tmp_path / "other.pdf").write_bytes(b"released document")
    (tmp_path / "private.pdf").write_bytes(b"private document")
    db_session.add(third_contact)
    await db_session.flush()
    db_session.add(other_party)
    await db_session.flush()
    db_session.add(third_party)
    await db_session.flush()
    db_session.add(third_invite)
    await db_session.flush()
    db_session.add_all(
        [
            document,
            hidden_document,
            private_document,
            proposal,
            hidden_proposal,
            own_asset,
            private_asset,
            shared_asset,
        ]
    )
    await db_session.flush()
    db_session.add_all(
        [
            MediationDocumentRecipient(
                id=uuid.uuid4(),
                tenant_id=fixture["matter"].tenant_id,
                document_id=document.id,
                party_id=other_party.id,
            ),
            MediationDocumentRecipient(
                id=uuid.uuid4(),
                tenant_id=fixture["matter"].tenant_id,
                document_id=hidden_document.id,
                party_id=fixture["party"].id,
            ),
            MediationProposalRecipient(
                id=uuid.uuid4(),
                tenant_id=fixture["matter"].tenant_id,
                proposal_id=hidden_proposal.id,
                party_id=fixture["party"].id,
            ),
            MediationProposalRecipient(
                id=uuid.uuid4(),
                tenant_id=fixture["matter"].tenant_id,
                proposal_id=proposal.id,
                party_id=other_party.id,
            ),
        ]
    )
    await db_session.commit()
    response = await client.get(f"{PORTAL}/mediation", headers=fixture["headers"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert {row["filename"] for row in body["documents"]} == {"my.pdf", "other.pdf"}
    assert {row["title"] for row in body["proposals"]} == {"My offer", "Other offer"}
    assert {row["description"] for row in body["own_assets"]} == {"My draft"}
    assert {row["description"] for row in body["shared_assets"]} == {"Shared asset"}
    for row in body["documents"] + body["proposals"]:
        assert "recipient_party_ids" not in row

    own_download = await client.get(
        f"{PORTAL}/mediation/documents/{document.id}/download",
        headers=fixture["headers"],
    )
    assert own_download.status_code == 200
    assert own_download.content == b"my document"
    released_download = await client.get(
        f"{PORTAL}/mediation/documents/{hidden_document.id}/download",
        headers=fixture["headers"],
    )
    assert released_download.status_code == 200
    assert released_download.content == b"released document"
    (tmp_path / "my.pdf").write_bytes(b"tampered document")
    assert (
        await client.get(
            f"{PORTAL}/mediation/documents/{document.id}/download",
            headers=fixture["headers"],
        )
    ).status_code == 409
    assert (
        await client.get(
            f"{PORTAL}/mediation/documents/{private_document.id}/download",
            headers=fixture["headers"],
        )
    ).status_code == 404

    third_token = create_matter_portal_token(
        tenant_id=str(fixture["matter"].tenant_id),
        matter_id=str(fixture["matter"].id),
        contact_id=str(third_contact.id),
        email=third_contact.email,
        invite_id=str(third_invite.id),
    )
    third_headers = {
        "Cookie": f"{CLIENT_PORTAL_COOKIE_NAME}={third_token}",
        "Authorization": "",
    }
    # Direct download authorization is recipient-specific; another native
    # client on the matter cannot fetch a document released only to Jane.
    assert (
        await client.get(
            f"{PORTAL}/mediation/documents/{hidden_document.id}/download",
            headers=third_headers,
        )
    ).status_code == 404
