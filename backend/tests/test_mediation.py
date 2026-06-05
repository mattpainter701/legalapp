"""Tests for the Mediation Platform module (firm router + external portal)."""

import uuid

import pytest

from app.models.user import User
from app.services.portal_token import create_portal_token


pytestmark = pytest.mark.asyncio(loop_scope="session")


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


def _portal_headers(*, tenant_id, case_id, party_id, party_role):
    token = create_portal_token(
        tenant_id=str(tenant_id),
        case_id=str(case_id),
        party_id=str(party_id),
        party_role=party_role,
    )
    return {"Authorization": f"Bearer {token}"}


# ── Firm-side CRUD ────────────────────────────────────────────────────────────


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


async def test_parties_and_invite_creates_client_user(client, db_session):
    case = await _make_case(client)
    case_id = case["id"]
    party = await _add_party(
        client, case_id, "our_client", "Jane Doe", "jane@example.com"
    )

    # Inviting a firm client provisions a role="client" User and returns a link.
    resp = await client.post(
        f"/api/plugins/mediation/cases/{case_id}/parties/{party['id']}/invite"
    )
    assert resp.status_code == 200, resp.text
    invite = resp.json()
    assert invite["kind"] == "client_account"
    assert "/portal/accept?token=" in invite["invite_url"]

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

    client_hdrs = _portal_headers(
        tenant_id=test_tenant.id,
        case_id=case_id,
        party_id=client_party["id"],
        party_role="our_client",
    )
    opp_hdrs = _portal_headers(
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


async def test_opposing_cannot_send_or_edit_others(client, test_tenant):
    case = await _make_case(client)
    case_id = case["id"]
    client_party = await _add_party(client, case_id, "our_client", "Jane Doe")
    opposing_party = await _add_party(client, case_id, "opposing_party", "John Doe")

    client_hdrs = _portal_headers(
        tenant_id=test_tenant.id, case_id=case_id,
        party_id=client_party["id"], party_role="our_client",
    )
    opp_hdrs = _portal_headers(
        tenant_id=test_tenant.id, case_id=case_id,
        party_id=opposing_party["id"], party_role="opposing_party",
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
    raw_token = resp.json()["invite_url"].split("token=")[1]
    assert resp.json()["kind"] == "portal_magic"

    resp = await client.post(
        "/api/portal/mediation/accept", json={"token": raw_token}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["case_id"] == case_id
    assert resp.json()["party_role"] == "opposing_party"
    # The session cookie was set.
    assert "access_token" in resp.cookies

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
    a_hdrs = _portal_headers(
        tenant_id=test_tenant.id, case_id=case_id,
        party_id=a["id"], party_role="our_client",
    )
    b_hdrs = _portal_headers(
        tenant_id=test_tenant.id, case_id=case_id,
        party_id=b["id"], party_role="opposing_party",
    )

    resp = await client.post(
        "/api/portal/mediation/proposals",
        json={"title": "Offer 1", "body": "60/40 split"},
        headers=a_hdrs,
    )
    assert resp.status_code == 201
    p1 = resp.json()["id"]

    resp = await client.post(
        "/api/portal/mediation/proposals",
        json={"title": "Counter", "body": "50/50", "parent_proposal_id": p1},
        headers=b_hdrs,
    )
    assert resp.status_code == 201

    # Firm sees both; the parent is now superseded.
    resp = await client.get(f"/api/plugins/mediation/cases/{case_id}/proposals")
    proposals = {p["title"]: p for p in resp.json()}
    assert proposals["Offer 1"]["status"] == "superseded"
    assert proposals["Counter"]["status"] == "open"


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
        id=uuid.uuid4(), name="Other Firm", domain="other.com",
        billing_tier="payg", is_active=True,
    )
    other_user = User(
        id=uuid.uuid4(), tenant_id=other_tenant.id, email="other@other.com",
        full_name="Other", role="admin", is_active=True,
    )
    db_session.add_all([other_tenant, other_user])
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
