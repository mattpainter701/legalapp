"""Tests for the Mediation Platform module (firm router + external portal)."""

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio

from app.models.mediation import MediationInvite
from app.models.plugin import TenantPluginEntitlement
from app.models.rbac import Role, UserRole
from app.models.user import User
from app.services import email as email_module


pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest_asyncio.fixture(autouse=True)
async def _reset_mediation_portal_auth_limit(test_redis):
    """Keep source-IP auth throttling isolated between mediation tests."""
    keys = [
        key
        async for key in test_redis.scan_iter(
            match="rate:auth:/api/portal/mediation/accept:*"
        )
    ]
    if keys:
        await test_redis.delete(*keys)


@pytest_asyncio.fixture(autouse=True)
async def mediation_access(db_session, test_tenant, test_user):
    """Model a licensed firm and a live RBAC legal approver."""
    entitlement = TenantPluginEntitlement(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        plugin_name="mediation-legal",
        status="included",
        starts_at=datetime.now(timezone.utc),
    )
    approver_role = Role(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        name="Mediation Attorney",
        capabilities=["manage_matters", "manage_documents", "approve_legal_work"],
        is_system=False,
    )
    assignment = UserRole(
        id=uuid.uuid4(),
        user_id=test_user.id,
        role_id=approver_role.id,
        tenant_id=test_tenant.id,
        source="manual",
    )
    db_session.add_all([entitlement, approver_role, assignment])
    await db_session.commit()
    return entitlement, approver_role


# ── Helpers ─────────────────────────────────────────────────────────────────


async def _make_case(client, **overrides):
    payload = {
        "case_name": "Doe v. Doe",
        "party_a": "Jane Doe",
        "party_b": "John Doe",
        "dispute_type": "Divorce / Property Division",
        "mediator": "Pat Mediator",
        "claim_value": "$450,000",
    }
    payload.update(overrides)
    resp = await client.post("/api/plugins/mediation/cases", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _add_party(client, case_id, role, name, email=None):
    resp = await client.post(
        f"/api/plugins/mediation/cases/{case_id}/parties",
        json={"name": name, "role": role, "email": email},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _portal_headers(client, *, tenant_id, case_id, party_id, party_role):
    invite_resp = await client.post(
        f"/api/plugins/mediation/cases/{case_id}/parties/{party_id}/invite"
    )
    assert invite_resp.status_code == 200, invite_resp.text
    raw = invite_resp.json()["invite_url"].split("token=")[1]
    accepted = await client.post("/api/portal/mediation/accept", json={"token": raw})
    assert accepted.status_code == 200, accepted.text
    token = accepted.cookies["mediation_portal_token"]
    client.cookies.pop("mediation_portal_token", None)
    return {"Authorization": f"Bearer {token}"}


# ── Firm-side CRUD ────────────────────────────────────────────────────────────


async def test_mediation_requires_an_active_paid_entitlement(
    client,
    db_session,
    test_tenant,
    mediation_access,
):
    entitlement, _ = mediation_access
    case = await _make_case(client)
    active_party = await _add_party(client, case["id"], "our_client", "Jane Doe")
    active_portal_headers = await _portal_headers(
        client,
        tenant_id=test_tenant.id,
        case_id=case["id"],
        party_id=active_party["id"],
        party_role="our_client",
    )
    party = await _add_party(
        client,
        case["id"],
        "opposing_party",
        "John Doe",
        "john@example.com",
    )
    invite = await client.post(
        f"/api/plugins/mediation/cases/{case['id']}/parties/{party['id']}/invite"
    )
    raw_token = invite.json()["invite_url"].split("token=")[1]

    entitlement.status = "disabled"
    await db_session.commit()

    firm = await client.get("/api/plugins/mediation/cases")
    assert firm.status_code == 403
    assert "turned off" in firm.json()["detail"]
    existing_portal = await client.get(
        "/api/portal/mediation/case",
        headers=active_portal_headers,
    )
    assert existing_portal.status_code == 404
    rejected_invite = await client.post(
        "/api/portal/mediation/accept",
        json={"token": raw_token},
    )
    assert rejected_invite.status_code == 404
    stored_invite = await db_session.get(
        MediationInvite, uuid.UUID(invite.json()["id"])
    )
    assert stored_invite.accepted_at is None

    await db_session.delete(entitlement)
    await db_session.commit()
    missing = await client.get("/api/plugins/mediation/cases")
    assert missing.status_code == 402


async def test_non_approver_can_prepare_but_cannot_approve_or_release(
    client,
    db_session,
    test_user,
    test_tenant,
    mediation_access,
):
    _, approver_role = mediation_access
    case = await _make_case(client)
    case_id = case["id"]
    party_a = await _add_party(client, case_id, "our_client", "Jane Doe")
    party_b = await _add_party(client, case_id, "opposing_party", "John Doe")
    party_headers = await _portal_headers(
        client,
        tenant_id=test_tenant.id,
        case_id=case_id,
        party_id=party_a["id"],
        party_role="our_client",
    )
    asset = await client.post(
        "/api/portal/mediation/assets",
        json={"description": "Retirement account", "value": "100000"},
        headers=party_headers,
    )
    asset_id = asset.json()["id"]
    assert (
        await client.post(
            f"/api/portal/mediation/assets/{asset_id}/submit",
            headers=party_headers,
        )
    ).status_code == 200
    document = await client.post(
        "/api/portal/mediation/documents/upload",
        files={"file": ("private.txt", b"private evidence", "text/plain")},
        headers=party_headers,
    )
    proposal = await client.post(
        "/api/portal/mediation/proposals",
        json={"title": "Initial offer", "body": "60/40 split"},
        headers=party_headers,
    )

    approver_role.capabilities = ["manage_matters", "manage_documents"]
    test_user.professional_role = "Attorney"
    await db_session.commit()

    assert (await client.get("/api/plugins/mediation/cases")).status_code == 200
    protected_calls = [
        (
            f"/api/plugins/mediation/cases/{case_id}/assets/{asset_id}/approve",
            None,
        ),
        (
            f"/api/plugins/mediation/cases/{case_id}/assets/{asset_id}/send",
            None,
        ),
        (
            f"/api/plugins/mediation/cases/{case_id}/documents/{document.json()['id']}/release",
            {"party_ids": [party_b["id"]]},
        ),
        (
            f"/api/plugins/mediation/cases/{case_id}/proposals/{proposal.json()['id']}/review",
            {"decision": "approved"},
        ),
        (
            f"/api/plugins/mediation/cases/{case_id}/proposals/{proposal.json()['id']}/release",
            {"party_ids": [party_b["id"]]},
        ),
    ]
    for path, payload in protected_calls:
        response = (
            await client.post(path, json=payload)
            if payload is not None
            else await client.post(path)
        )
        assert response.status_code == 403
        assert response.json()["detail"] == (
            "Legal approval authority is required for this action"
        )


async def test_case_crud_and_sessions(client):
    case = await _make_case(client)
    case_id = case["id"]
    assert case["case_name"] == "Doe v. Doe"
    assert case["title"] == "Doe v. Doe"
    assert case["status"] == "active"

    # list
    resp = await client.get("/api/plugins/mediation/cases")
    assert resp.status_code == 200
    assert any(c["id"] == case_id for c in resp.json())

    # detail wrapper shape {mediation, sessions}
    resp = await client.get(f"/api/plugins/mediation/cases/{case_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["mediation"]["id"] == case_id
    assert body["sessions"] == []

    # add a session
    resp = await client.post(
        f"/api/plugins/mediation/cases/{case_id}/events",
        json={"session_type": "caucus", "title": "Initial caucus", "content": "Notes"},
    )
    assert resp.status_code == 201
    sess = resp.json()
    assert sess["session_type"] == "caucus"
    assert sess["added_by"]  # stamped with the attorney's name

    # update
    resp = await client.patch(
        f"/api/plugins/mediation/cases/{case_id}",
        json={"status": "scheduled", "case_name": "Doe v. Doe (2026)"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "scheduled"
    assert resp.json()["title"] == "Doe v. Doe (2026)"

    # stats
    resp = await client.get("/api/plugins/mediation/cases/stats")
    assert resp.status_code == 200
    assert resp.json()["total"] >= 1

    # delete
    resp = await client.delete(f"/api/plugins/mediation/cases/{case_id}")
    assert resp.status_code == 204
    resp = await client.get(f"/api/plugins/mediation/cases/{case_id}")
    assert resp.status_code == 404


async def test_parties_and_invite_creates_client_user(client, db_session, monkeypatch):
    case = await _make_case(client)
    case_id = case["id"]
    party = await _add_party(
        client, case_id, "our_client", "Jane Doe", "jane@example.com"
    )

    monkeypatch.setattr(email_module.settings, "EMAIL_ENABLED", False)

    # Inviting a firm client provisions a role="client" User and returns a link,
    # while reporting that disabled email did not deliver it.
    resp = await client.post(
        f"/api/plugins/mediation/cases/{case_id}/parties/{party['id']}/invite"
    )
    assert resp.status_code == 200, resp.text
    invite = resp.json()
    assert invite["kind"] == "client_account"
    assert "/portal/accept?token=" in invite["invite_url"]
    assert invite["email_sent"] is False
    assert "outbound email is unavailable" in invite["delivery_error"]

    result = await db_session.execute(
        User.__table__.select().where(User.email == "jane@example.com")
    )
    row = result.first()
    assert row is not None and row.role == "client"

    # The party now reflects an account + invited flag.
    resp = await client.get(f"/api/plugins/mediation/cases/{case_id}/parties")
    me = next(p for p in resp.json() if p["id"] == party["id"])
    assert me["has_account"] is True
    assert me["invited"] is True


# ── Approval workflow (portal + firm) ──────────────────────────────────────────


async def test_full_approval_workflow(client, test_tenant):
    case = await _make_case(client)
    case_id = case["id"]
    client_party = await _add_party(client, case_id, "our_client", "Jane Doe")
    opposing_party = await _add_party(client, case_id, "opposing_party", "John Doe")

    client_hdrs = await _portal_headers(
        client,
        tenant_id=test_tenant.id,
        case_id=case_id,
        party_id=client_party["id"],
        party_role="our_client",
    )
    opp_hdrs = await _portal_headers(
        client,
        tenant_id=test_tenant.id,
        case_id=case_id,
        party_id=opposing_party["id"],
        party_role="opposing_party",
    )

    # Client discloses an asset via the portal (draft) then submits.
    resp = await client.post(
        "/api/portal/mediation/assets",
        json={"description": "Marital home", "value": "350000", "owned_by": "joint"},
        headers=client_hdrs,
    )
    assert resp.status_code == 201, resp.text
    asset_id = resp.json()["id"]
    assert resp.json()["status"] == "draft"

    resp = await client.post(
        f"/api/portal/mediation/assets/{asset_id}/submit", headers=client_hdrs
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "submitted"

    # Opposing party must NOT see a merely-submitted asset yet.
    resp = await client.get("/api/portal/mediation/case", headers=opp_hdrs)
    assert resp.status_code == 200
    assert resp.json()["shared_assets"] == []

    # Attorney approves, then sends to the opposing party.
    resp = await client.post(
        f"/api/plugins/mediation/cases/{case_id}/assets/{asset_id}/approve"
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "attorney_approved"

    # Cannot send before approval is respected: re-approve idempotent guard
    resp = await client.post(
        f"/api/plugins/mediation/cases/{case_id}/assets/{asset_id}/send"
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "sent"

    # Now the opposing party sees it as a shared asset.
    resp = await client.get("/api/portal/mediation/case", headers=opp_hdrs)
    shared = resp.json()["shared_assets"]
    assert len(shared) == 1 and shared[0]["id"] == asset_id

    # Opposing disputes it.
    resp = await client.post(
        f"/api/portal/mediation/assets/{asset_id}/decision",
        json={"decision": "disputed", "dispute_reason": "Valuation too low"},
        headers=opp_hdrs,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "disputed"
    assert resp.json()["dispute_reason"] == "Valuation too low"

    # The case audit log captured approve + send events.
    resp = await client.get(f"/api/plugins/mediation/cases/{case_id}")
    titles = [s["title"] for s in resp.json()["sessions"]]
    assert any("approved" in t.lower() for t in titles)
    assert any("sent" in t.lower() for t in titles)


async def test_approved_and_sent_assets_are_immutable(client, test_tenant):
    case = await _make_case(client)
    case_id = case["id"]
    client_party = await _add_party(client, case_id, "our_client", "Jane Doe")
    party_headers = await _portal_headers(
        client,
        tenant_id=test_tenant.id,
        case_id=case_id,
        party_id=client_party["id"],
        party_role="our_client",
    )
    created = await client.post(
        "/api/portal/mediation/assets",
        json={"description": "Retirement account", "value": "100000"},
        headers=party_headers,
    )
    assert created.status_code == 201
    asset_id = created.json()["id"]
    submitted = await client.post(
        f"/api/portal/mediation/assets/{asset_id}/submit", headers=party_headers
    )
    assert submitted.status_code == 200
    approved = await client.post(
        f"/api/plugins/mediation/cases/{case_id}/assets/{asset_id}/approve"
    )
    assert approved.status_code == 200
    before = approved.json()

    # Approval freezes the record, even before it is released externally.
    changed = await client.patch(
        f"/api/plugins/mediation/cases/{case_id}/assets/{asset_id}",
        json={"description": "Tampered account", "value": "1"},
    )
    assert changed.status_code == 409
    sent = await client.post(
        f"/api/plugins/mediation/cases/{case_id}/assets/{asset_id}/send"
    )
    assert sent.status_code == 200
    deleted = await client.delete(
        f"/api/plugins/mediation/cases/{case_id}/assets/{asset_id}"
    )
    assert deleted.status_code == 409
    current = (
        await client.get(f"/api/plugins/mediation/cases/{case_id}/assets")
    ).json()
    record = next(item for item in current if item["id"] == asset_id)
    assert record["description"] == before["description"]
    assert record["value"] == before["value"]
    assert record["status"] == "sent"


async def test_party_documents_are_private_until_firm_release_and_then_immutable(
    client, test_tenant
):
    case = await _make_case(client)
    case_id = case["id"]
    party_a = await _add_party(client, case_id, "our_client", "Jane Doe")
    party_b = await _add_party(client, case_id, "opposing_party", "John Doe")
    a_headers = await _portal_headers(
        client,
        tenant_id=test_tenant.id,
        case_id=case_id,
        party_id=party_a["id"],
        party_role="our_client",
    )
    b_headers = await _portal_headers(
        client,
        tenant_id=test_tenant.id,
        case_id=case_id,
        party_id=party_b["id"],
        party_role="opposing_party",
    )
    uploaded = await client.post(
        "/api/portal/mediation/documents/upload",
        files={"file": ("private.txt", b"private evidence", "text/plain")},
        headers=a_headers,
    )
    assert uploaded.status_code == 201, uploaded.text
    document = uploaded.json()
    doc_id = document["id"]
    assert document["is_released"] is False
    a_docs = (
        await client.get("/api/portal/mediation/documents", headers=a_headers)
    ).json()
    b_docs = (
        await client.get("/api/portal/mediation/documents", headers=b_headers)
    ).json()
    assert doc_id in {row["id"] for row in a_docs}
    assert doc_id not in {row["id"] for row in b_docs}
    assert (
        await client.get(
            f"/api/portal/mediation/documents/{doc_id}/download",
            headers=b_headers,
        )
    ).status_code == 404
    redundant_release = await client.post(
        f"/api/plugins/mediation/cases/{case_id}/documents/{doc_id}/release",
        json={"party_ids": [party_a["id"]]},
    )
    assert redundant_release.status_code == 400

    released = await client.post(
        f"/api/plugins/mediation/cases/{case_id}/documents/{doc_id}/release",
        json={"party_ids": [party_b["id"]]},
    )
    assert released.status_code == 200, released.text
    assert released.json()["is_released"] is True
    b_docs = (
        await client.get("/api/portal/mediation/documents", headers=b_headers)
    ).json()
    received = next(row for row in b_docs if row["id"] == doc_id)
    assert received["uploaded_by_party_id"] is None
    assert received["uploaded_by_user_id"] is None
    assert received["recipient_party_ids"] == [party_b["id"]]
    downloaded = await client.get(
        f"/api/portal/mediation/documents/{doc_id}/download", headers=b_headers
    )
    assert downloaded.status_code == 200
    assert downloaded.content == b"private evidence"
    assert (
        await client.delete(
            f"/api/plugins/mediation/cases/{case_id}/documents/{doc_id}"
        )
    ).status_code == 409


async def test_opposing_cannot_send_or_edit_others(client, test_tenant):
    case = await _make_case(client)
    case_id = case["id"]
    client_party = await _add_party(client, case_id, "our_client", "Jane Doe")
    opposing_party = await _add_party(client, case_id, "opposing_party", "John Doe")

    client_hdrs = await _portal_headers(
        client,
        tenant_id=test_tenant.id,
        case_id=case_id,
        party_id=client_party["id"],
        party_role="our_client",
    )
    opp_hdrs = await _portal_headers(
        client,
        tenant_id=test_tenant.id,
        case_id=case_id,
        party_id=opposing_party["id"],
        party_role="opposing_party",
    )

    resp = await client.post(
        "/api/portal/mediation/assets",
        json={"description": "Pension"},
        headers=client_hdrs,
    )
    asset_id = resp.json()["id"]

    # Opposing party can't edit another party's asset.
    resp = await client.patch(
        f"/api/portal/mediation/assets/{asset_id}",
        json={"description": "hacked"},
        headers=opp_hdrs,
    )
    assert resp.status_code == 403

    # Opposing party can't decide on something that isn't 'sent'.
    resp = await client.post(
        f"/api/portal/mediation/assets/{asset_id}/decision",
        json={"decision": "approved"},
        headers=opp_hdrs,
    )
    assert resp.status_code == 409


async def test_portal_session_uses_the_partys_current_role(client, test_tenant):
    case = await _make_case(client)
    case_id = case["id"]
    client_party = await _add_party(client, case_id, "our_client", "Jane Doe")
    opposing_party = await _add_party(client, case_id, "opposing_party", "John Doe")
    client_headers = await _portal_headers(
        client,
        tenant_id=test_tenant.id,
        case_id=case_id,
        party_id=client_party["id"],
        party_role="our_client",
    )
    opposing_headers = await _portal_headers(
        client,
        tenant_id=test_tenant.id,
        case_id=case_id,
        party_id=opposing_party["id"],
        party_role="opposing_party",
    )
    asset = await client.post(
        "/api/portal/mediation/assets",
        json={"description": "Marital home"},
        headers=client_headers,
    )
    asset_id = asset.json()["id"]
    assert (
        await client.post(
            f"/api/portal/mediation/assets/{asset_id}/submit",
            headers=client_headers,
        )
    ).status_code == 200
    assert (
        await client.post(
            f"/api/plugins/mediation/cases/{case_id}/assets/{asset_id}/approve"
        )
    ).status_code == 200
    assert (
        await client.post(
            f"/api/plugins/mediation/cases/{case_id}/assets/{asset_id}/send"
        )
    ).status_code == 200

    changed_role = await client.patch(
        f"/api/plugins/mediation/cases/{case_id}/parties/{opposing_party['id']}",
        json={"role": "our_client"},
    )
    assert changed_role.status_code == 200

    stale_role_decision = await client.post(
        f"/api/portal/mediation/assets/{asset_id}/decision",
        json={"decision": "approved"},
        headers=opposing_headers,
    )
    assert stale_role_decision.status_code == 403


# ── Portal invite acceptance ────────────────────────────────────────────────


async def test_accept_invite_issues_portal_session(client, test_tenant):
    case = await _make_case(client)
    case_id = case["id"]
    party = await _add_party(
        client, case_id, "opposing_party", "John Doe", "john@example.com"
    )
    resp = await client.post(
        f"/api/plugins/mediation/cases/{case_id}/parties/{party['id']}/invite"
    )
    assert resp.status_code == 200
    invite_id = resp.json()["id"]
    raw_token = resp.json()["invite_url"].split("token=")[1]
    assert resp.json()["kind"] == "portal_magic"

    resp = await client.post("/api/portal/mediation/accept", json={"token": raw_token})
    assert resp.status_code == 200, resp.text
    assert resp.json()["case_id"] == case_id
    assert resp.json()["party_role"] == "opposing_party"
    # The session cookie was set.
    assert "mediation_portal_token" in resp.cookies
    assert "access_token" not in resp.cookies

    replay = await client.post(
        "/api/portal/mediation/accept", json={"token": raw_token}
    )
    # Public invite failures are deliberately indistinguishable so the opaque
    # token exchange is not a validity/revocation/tenant-state oracle.
    assert replay.status_code == 404
    assert replay.json()["detail"] == "Invite not found or unavailable"

    revoked = await client.delete(
        f"/api/plugins/mediation/cases/{case_id}/parties/{party['id']}"
        f"/invites/{invite_id}"
    )
    assert revoked.status_code == 204, revoked.text
    portal_after_revoke = await client.get("/api/portal/mediation/case")
    assert portal_after_revoke.status_code == 401

    # A bogus token is rejected.
    resp = await client.post(
        "/api/portal/mediation/accept", json={"token": "not-a-real-token"}
    )
    assert resp.status_code == 404


# ── Proposals ─────────────────────────────────────────────────────────────────


async def test_proposal_counter_chain(client, test_tenant):
    case = await _make_case(client)
    case_id = case["id"]
    a = await _add_party(client, case_id, "our_client", "Jane Doe")
    b = await _add_party(client, case_id, "opposing_party", "John Doe")
    a_hdrs = await _portal_headers(
        client,
        tenant_id=test_tenant.id,
        case_id=case_id,
        party_id=a["id"],
        party_role="our_client",
    )
    b_hdrs = await _portal_headers(
        client,
        tenant_id=test_tenant.id,
        case_id=case_id,
        party_id=b["id"],
        party_role="opposing_party",
    )

    resp = await client.post(
        "/api/portal/mediation/proposals",
        json={"title": "Offer 1", "body": "60/40 split"},
        headers=a_hdrs,
    )
    assert resp.status_code == 201
    assert resp.json()["proposed_by_name"] == "Jane Doe"
    p1 = resp.json()["id"]

    # A proposal is private to its author until the firm reviews and releases it.
    resp = await client.get("/api/portal/mediation/proposals", headers=b_hdrs)
    assert resp.status_code == 200 and resp.json() == []
    resp = await client.post(
        f"/api/plugins/mediation/cases/{case_id}/proposals/{p1}/release",
        json={"party_ids": [b["id"]]},
    )
    assert resp.status_code == 409
    resp = await client.post(
        f"/api/plugins/mediation/cases/{case_id}/proposals/{p1}/review",
        json={"decision": "approved"},
    )
    assert resp.status_code == 200
    resp = await client.post(
        f"/api/plugins/mediation/cases/{case_id}/proposals/{p1}/release",
        json={"party_ids": [b["id"]]},
    )
    assert resp.status_code == 200
    received = (
        await client.get("/api/portal/mediation/proposals", headers=b_hdrs)
    ).json()[0]
    assert received["proposed_by_party_id"] is None
    assert received["created_by_user_id"] is None
    assert received["reviewed_by_user_id"] is None
    assert received["released_by_user_id"] is None
    assert received["recipient_party_ids"] == [b["id"]]

    resp = await client.post(
        "/api/portal/mediation/proposals",
        json={"title": "Counter", "body": "50/50", "parent_proposal_id": p1},
        headers=b_hdrs,
    )
    assert resp.status_code == 201
    counter_id = resp.json()["id"]

    # The parent is not superseded merely by drafting a counterproposal.
    resp = await client.get(f"/api/plugins/mediation/cases/{case_id}/proposals")
    proposals = {p["title"]: p for p in resp.json()}
    assert proposals["Offer 1"]["status"] == "open"

    # A counterproposal also requires review and release before it supersedes
    # the parent and becomes visible to the other party.
    resp = await client.get("/api/portal/mediation/proposals", headers=a_hdrs)
    assert resp.status_code == 200
    assert all(p["id"] != counter_id for p in resp.json())
    resp = await client.post(
        f"/api/plugins/mediation/cases/{case_id}/proposals/{counter_id}/review",
        json={"decision": "approved"},
    )
    assert resp.status_code == 200
    resp = await client.post(
        f"/api/plugins/mediation/cases/{case_id}/proposals/{counter_id}/release",
        json={"party_ids": [a["id"]]},
    )
    assert resp.status_code == 200

    # Firm sees both; only release of the counter supersedes its parent.
    resp = await client.get(f"/api/plugins/mediation/cases/{case_id}/proposals")
    proposals = {p["title"]: p for p in resp.json()}
    assert proposals["Offer 1"]["status"] == "superseded"
    assert proposals["Counter"]["status"] == "open"

    # Once one reviewed counter is released, neither portal users nor firm
    # staff can branch a second counter from the superseded parent.
    stale_portal_counter = await client.post(
        "/api/portal/mediation/proposals",
        json={
            "title": "Stale portal counter",
            "body": "55/45",
            "parent_proposal_id": p1,
        },
        headers=b_hdrs,
    )
    assert stale_portal_counter.status_code == 409
    stale_firm_counter = await client.post(
        f"/api/plugins/mediation/cases/{case_id}/proposals",
        json={
            "title": "Stale firm counter",
            "parent_proposal_id": p1,
            "proposed_by_party_id": b["id"],
        },
    )
    assert stale_firm_counter.status_code == 409
    assert (
        await client.post(
            f"/api/plugins/mediation/cases/{case_id}/proposals/{p1}/review",
            json={"decision": "approved"},
        )
    ).status_code == 409
    assert (
        await client.post(
            f"/api/plugins/mediation/cases/{case_id}/proposals/{p1}/release",
            json={"party_ids": [a["id"]]},
        )
    ).status_code == 409


async def test_proposal_parent_must_belong_to_same_case(client, test_tenant):
    first = await _make_case(client, case_name="First mediation")
    second = await _make_case(client, case_name="Second mediation")
    party = await _add_party(client, first["id"], "our_client", "Jane Doe")
    other_party = await _add_party(client, second["id"], "our_client", "John Doe")
    p1 = await client.post(
        "/api/plugins/mediation/cases/%s/proposals" % first["id"],
        json={"title": "First offer", "proposed_by_party_id": party["id"]},
    )
    assert p1.status_code == 201
    cross_case = await client.post(
        "/api/plugins/mediation/cases/%s/proposals" % second["id"],
        json={
            "title": "Invalid counter",
            "parent_proposal_id": p1.json()["id"],
            "proposed_by_party_id": other_party["id"],
        },
    )
    assert cross_case.status_code == 404
    proposals = (
        await client.get(f"/api/plugins/mediation/cases/{first['id']}/proposals")
    ).json()
    assert proposals[0]["status"] == "open"


# ── Tenant isolation ────────────────────────────────────────────────────────


async def test_tenant_isolation(client, db_session, test_tenant):
    from datetime import datetime, timedelta, timezone

    from jose import jwt as jose_jwt

    from app.config import get_settings
    from app.models.tenant import Tenant

    case = await _make_case(client)
    case_id = case["id"]

    # A second tenant + user must not see the first tenant's case.
    settings = get_settings()
    other_tenant = Tenant(
        id=uuid.uuid4(),
        name="Other Firm",
        domain="other.com",
        billing_tier="payg",
        is_active=True,
    )
    other_user = User(
        id=uuid.uuid4(),
        tenant_id=other_tenant.id,
        email="other@other.com",
        full_name="Other",
        role="admin",
        is_active=True,
    )
    other_entitlement = TenantPluginEntitlement(
        id=uuid.uuid4(),
        tenant_id=other_tenant.id,
        plugin_name="mediation-legal",
        status="included",
    )
    db_session.add_all([other_tenant, other_user, other_entitlement])
    await db_session.commit()

    other_token = jose_jwt.encode(
        {
            "sub": str(other_user.id),
            "tenant_id": str(other_tenant.id),
            "role": "admin",
            "email": other_user.email,
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        },
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )
    resp = await client.get(
        f"/api/plugins/mediation/cases/{case_id}",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert resp.status_code == 404
