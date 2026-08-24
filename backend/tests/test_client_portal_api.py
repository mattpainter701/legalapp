"""End-to-end coverage for the client portal's client-facing surface.

These drive the real ASGI app with a real portal cookie, because the portal is
the one authenticated surface in the product with no ``User`` row behind it —
its scoping, revocation, and input limits only mean something when exercised
through the actual dependency chain.
"""

import hashlib
import io
import secrets
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch

from app.models.billing import Invoice, Payment
from app.models.client_portal import ClientPortalInvite
from app.models.communication_log import CommunicationLog
from app.models.matter_assignment import MatterAssignment
from app.models.plugin import Matter
from app.routers.client_portal import CLIENT_PORTAL_COOKIE_NAME
from app.services.portal_token import create_matter_portal_token

PORTAL = "/api/portal/client"


@pytest_asyncio.fixture
async def portal_matter(db_session, test_tenant, test_user):
    matter = Matter(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        slug=f"portal-matter-{uuid.uuid4().hex[:8]}",
        matter_name="Rivera v. Northline Freight",
        matter_type="litigation",
        status="open",
        stage="discovery",
        practice_area="Personal Injury",
        description="Rear-end collision on I-94.",
        key_dates={
            "mediation": "2019-04-01",
            "trial_date": (date.today() + timedelta(days=30)).isoformat(),
            "status_conference": (date.today() + timedelta(days=10)).isoformat(),
            "venue_note": "Cook County",
        },
        portal_enabled=True,
    )
    db_session.add(matter)
    db_session.add(
        MatterAssignment(
            id=uuid.uuid4(),
            tenant_id=test_tenant.id,
            matter_id=matter.id,
            user_id=test_user.id,
            role="lead",
            is_primary=True,
        )
    )
    await db_session.commit()
    await db_session.refresh(matter)
    return matter


@pytest_asyncio.fixture
async def portal_invite(db_session, test_tenant, portal_matter):
    raw_token = secrets.token_urlsafe(32)
    invite = ClientPortalInvite(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        matter_id=portal_matter.id,
        contact_id=None,
        token_hash=hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
        email="client@example.com",
        expires_at=datetime.now(timezone.utc) + timedelta(days=14),
    )
    db_session.add(invite)
    await db_session.commit()
    await db_session.refresh(invite)
    invite.raw_token = raw_token
    return invite


@pytest.fixture
def portal_cookie(test_tenant, portal_matter, portal_invite):
    """A minted portal session cookie for the fixture matter."""
    return create_matter_portal_token(
        tenant_id=str(test_tenant.id),
        matter_id=str(portal_matter.id),
        contact_id=None,
        email=portal_invite.email,
        invite_id=str(portal_invite.id),
    )


def _portal_headers(token: str) -> dict:
    # The portal cookie is the real transport; the Authorization header the
    # shared ``client`` fixture sets is a firm token and must not be sent here.
    return {"Cookie": f"{CLIENT_PORTAL_COOKIE_NAME}={token}", "Authorization": ""}


# ── Session and sign-out ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_session_reports_identity_and_expiry(client, portal_cookie, portal_matter):
    resp = await client.get(f"{PORTAL}/session", headers=_portal_headers(portal_cookie))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["matter_id"] == str(portal_matter.id)
    assert body["matter_name"] == portal_matter.matter_name
    assert body["email"] == "client@example.com"
    assert datetime.fromisoformat(body["expires_at"]) > datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_session_requires_a_portal_token(client):
    resp = await client.get(f"{PORTAL}/session", headers={"Authorization": ""})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_logout_revokes_the_session_for_reuse(client, portal_cookie):
    headers = _portal_headers(portal_cookie)
    assert (await client.get(f"{PORTAL}/session", headers=headers)).status_code == 200

    logout = await client.post(f"{PORTAL}/logout", headers=headers)
    assert logout.status_code == 204

    # A stolen copy of the same cookie must not survive the sign-out.
    replay = await client.get(f"{PORTAL}/session", headers=headers)
    assert replay.status_code == 401
    assert "revoked" in replay.json()["detail"].lower()


@pytest.mark.asyncio
async def test_logout_without_a_token_still_succeeds(client):
    resp = await client.post(f"{PORTAL}/logout", headers={"Authorization": ""})
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_revoked_invite_kills_a_live_session(
    client, db_session, portal_cookie, portal_invite
):
    portal_invite.revoked = True
    await db_session.commit()
    resp = await client.get(f"{PORTAL}/session", headers=_portal_headers(portal_cookie))
    assert resp.status_code == 401


# ── Matter overview ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_matter_view_parses_and_orders_key_dates(client, portal_cookie):
    resp = await client.get(f"{PORTAL}/matter", headers=_portal_headers(portal_cookie))
    assert resp.status_code == 200, resp.text
    body = resp.json()

    labels = [k["label"] for k in body["key_date_list"]]
    # Dated entries sort chronologically; the undated firm note trails behind.
    assert labels == ["Mediation", "Status conference", "Trial date", "Venue note"]
    assert body["key_date_list"][0]["is_past"] is True
    assert body["key_date_list"][-1]["iso_date"] is None

    # The next date is the soonest one still ahead, not the first in the map.
    assert body["next_key_date"]["label"] == "Status conference"
    assert body["next_key_date"]["days_away"] == 10

    assert body["attorneys"][0]["role"] == "lead"
    assert body["attorneys"][0]["email"] == "attorney@testfirm.com"


@pytest.mark.asyncio
async def test_matter_view_is_hidden_when_the_portal_is_off(
    client, db_session, portal_matter, portal_cookie
):
    portal_matter.portal_enabled = False
    await db_session.commit()
    resp = await client.get(f"{PORTAL}/matter", headers=_portal_headers(portal_cookie))
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_matter_view_counts_unread_firm_messages(
    client, db_session, test_tenant, portal_matter, portal_cookie
):
    db_session.add(
        CommunicationLog(
            id=uuid.uuid4(),
            tenant_id=test_tenant.id,
            matter_id=portal_matter.id,
            direction="outbound",
            channel="portal",
            status="sent",
            subject="Deposition scheduling",
            body="Are you free on the 14th?",
        )
    )
    await db_session.commit()

    headers = _portal_headers(portal_cookie)
    body = (await client.get(f"{PORTAL}/matter", headers=headers)).json()
    assert body["unread_message_count"] == 1

    read = await client.post(f"{PORTAL}/messages/read", headers=headers)
    assert read.status_code == 200
    assert read.json()["unread_count"] == 0

    body = (await client.get(f"{PORTAL}/matter", headers=headers)).json()
    assert body["unread_message_count"] == 0


# ── Messages ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_messages_round_trip_and_read_state(client, portal_cookie):
    headers = _portal_headers(portal_cookie)
    sent = await client.post(
        f"{PORTAL}/messages", headers=headers, json={"body": "  Any update?  "}
    )
    assert sent.status_code == 201, sent.text
    # Surrounding whitespace is stripped at the edge, not stored.
    assert sent.json()["body"] == "Any update?"
    assert sent.json()["direction"] == "inbound"

    listing = await client.get(f"{PORTAL}/messages", headers=headers)
    assert listing.status_code == 200
    body = listing.json()
    assert body["total"] == 1
    assert body["has_more"] is False
    # The client's own message is never counted as unread against them.
    assert body["unread_count"] == 0
    assert body["messages"][0]["unread"] is False


@pytest.mark.asyncio
async def test_sending_a_message_alerts_the_assigned_legal_team(
    client, portal_cookie, test_user
):
    with patch(
        "app.routers.client_portal.send_client_portal_message_alert",
        new_callable=AsyncMock,
    ) as alert:
        resp = await client.post(
            f"{PORTAL}/messages",
            headers=_portal_headers(portal_cookie),
            json={"body": "Please call me about the settlement offer."},
        )
    assert resp.status_code == 201
    alert.assert_awaited_once()
    kwargs = alert.await_args.kwargs
    assert kwargs["to_emails"] == [test_user.email]
    assert kwargs["sender"] == "client@example.com"


@pytest.mark.asyncio
async def test_a_failing_alert_never_costs_the_client_their_message(
    client, portal_cookie
):
    """The message is already persisted; mail trouble must not surface as a
    failed send to the client."""
    with patch(
        "app.routers.client_portal.send_client_portal_message_alert",
        new_callable=AsyncMock,
        side_effect=RuntimeError("smtp down"),
    ):
        resp = await client.post(
            f"{PORTAL}/messages",
            headers=_portal_headers(portal_cookie),
            json={"body": "Are we still on for Friday?"},
        )
    assert resp.status_code == 201

    listing = await client.get(
        f"{PORTAL}/messages", headers=_portal_headers(portal_cookie)
    )
    assert listing.json()["total"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"body": ""},
        {"body": "   "},
        {"body": "x" * 10_001},
        {"body": "hello", "subject": "s" * 201},
    ],
)
async def test_messages_reject_out_of_bounds_input(client, portal_cookie, payload):
    resp = await client.post(
        f"{PORTAL}/messages", headers=_portal_headers(portal_cookie), json=payload
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_messages_paginate_from_the_newest_end(
    client, db_session, test_tenant, portal_matter, portal_cookie
):
    base = datetime.now(timezone.utc) - timedelta(days=10)
    for i in range(5):
        db_session.add(
            CommunicationLog(
                id=uuid.uuid4(),
                tenant_id=test_tenant.id,
                matter_id=portal_matter.id,
                direction="outbound",
                channel="portal",
                status="sent",
                subject=f"Update {i}",
                body=f"body {i}",
                occurred_at=base + timedelta(hours=i),
            )
        )
    await db_session.commit()

    headers = _portal_headers(portal_cookie)
    page = (await client.get(f"{PORTAL}/messages?limit=2", headers=headers)).json()
    assert page["total"] == 5
    assert page["has_more"] is True
    # A page holds the newest two, presented oldest-first the way a thread reads.
    assert [m["subject"] for m in page["messages"]] == ["Update 3", "Update 4"]
    assert all(m["unread"] for m in page["messages"])

    over_cap = await client.get(f"{PORTAL}/messages?limit=500", headers=headers)
    assert over_cap.status_code == 422


# ── Documents ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "filename",
    ["payload.exe", "run.sh", "macro.docm", "noextension", "archive.tar.gz"],
)
async def test_upload_refuses_file_types_outside_the_allowlist(
    client, portal_cookie, filename
):
    resp = await client.post(
        f"{PORTAL}/documents/upload",
        headers=_portal_headers(portal_cookie),
        files={"file": (filename, io.BytesIO(b"content"), "application/octet-stream")},
    )
    assert resp.status_code == 400
    assert "file type" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_upload_refuses_an_empty_file(client, portal_cookie):
    resp = await client.post(
        f"{PORTAL}/documents/upload",
        headers=_portal_headers(portal_cookie),
        files={"file": ("statement.pdf", io.BytesIO(b""), "application/pdf")},
    )
    assert resp.status_code == 400
    assert "empty" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_upload_strips_path_traversal_from_the_filename(client, portal_cookie):
    resp = await client.post(
        f"{PORTAL}/documents/upload",
        headers=_portal_headers(portal_cookie),
        files={
            "file": (
                "../../../etc/passwd.pdf",
                io.BytesIO(b"%PDF-1.4 statement"),
                "application/pdf",
            )
        },
        data={"description": "Bank statement"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["filename"] == "passwd.pdf"
    assert body["uploaded_by_client"] is True
    assert body["description"] == "Bank statement"

    listing = await client.get(
        f"{PORTAL}/documents", headers=_portal_headers(portal_cookie)
    )
    assert [d["filename"] for d in listing.json()] == ["passwd.pdf"]


# ── Invoices ────────────────────────────────────────────────────────────────


async def _add_invoice(db_session, tenant, matter, user, **kwargs):
    invoice = Invoice(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        matter_id=matter.id,
        invoice_number=kwargs.pop("invoice_number", f"INV-{uuid.uuid4().hex[:6]}"),
        status=kwargs.pop("status", "sent"),
        issue_date=kwargs.pop("issue_date", date.today() - timedelta(days=40)),
        due_date=kwargs.pop("due_date", date.today() - timedelta(days=10)),
        subtotal=kwargs.pop("subtotal", Decimal("1000.00")),
        total=kwargs.pop("total", Decimal("1000.00")),
        created_by=user.id,
        **kwargs,
    )
    db_session.add(invoice)
    await db_session.commit()
    return invoice


@pytest.mark.asyncio
async def test_invoices_report_balance_and_overdue_state(
    client, db_session, test_tenant, test_user, portal_matter, portal_cookie
):
    overdue = await _add_invoice(
        db_session, test_tenant, portal_matter, test_user, invoice_number="INV-001"
    )
    db_session.add(
        Payment(
            id=uuid.uuid4(),
            tenant_id=test_tenant.id,
            invoice_id=overdue.id,
            amount=Decimal("400.00"),
            payment_date=date.today(),
            method="card",
        )
    )
    await _add_invoice(
        db_session,
        test_tenant,
        portal_matter,
        test_user,
        invoice_number="INV-002",
        status="paid",
        total=Decimal("250.00"),
        subtotal=Decimal("250.00"),
    )
    await db_session.commit()

    body = (
        await client.get(f"{PORTAL}/invoices", headers=_portal_headers(portal_cookie))
    ).json()
    by_number = {i["invoice_number"]: i for i in body["invoices"]}

    unpaid = by_number["INV-001"]
    assert Decimal(unpaid["amount_paid"]) == Decimal("400.00")
    assert Decimal(unpaid["balance_due"]) == Decimal("600.00")
    assert unpaid["is_overdue"] is True
    assert unpaid["days_overdue"] == 10

    # A status of "paid" settles the balance even with no payment row behind it,
    # so a write-off never shows the client a phantom amount owing.
    settled = by_number["INV-002"]
    assert Decimal(settled["balance_due"]) == Decimal("0")
    assert settled["is_overdue"] is False

    assert Decimal(body["outstanding_balance"]) == Decimal("600.00")
    assert Decimal(body["overdue_balance"]) == Decimal("600.00")
    assert Decimal(body["total_paid"]) == Decimal("650.00")


@pytest.mark.asyncio
async def test_draft_invoices_are_never_visible_to_the_client(
    client, db_session, test_tenant, test_user, portal_matter, portal_cookie
):
    await _add_invoice(
        db_session,
        test_tenant,
        portal_matter,
        test_user,
        invoice_number="INV-DRAFT",
        status="draft",
    )
    body = (
        await client.get(f"{PORTAL}/invoices", headers=_portal_headers(portal_cookie))
    ).json()
    assert body["invoices"] == []
    assert Decimal(body["outstanding_balance"]) == Decimal("0")


@pytest.mark.asyncio
async def test_outstanding_balance_surfaces_on_the_overview(
    client, db_session, test_tenant, test_user, portal_matter, portal_cookie
):
    await _add_invoice(
        db_session, test_tenant, portal_matter, test_user, invoice_number="INV-010"
    )
    body = (
        await client.get(f"{PORTAL}/matter", headers=_portal_headers(portal_cookie))
    ).json()
    assert Decimal(body["outstanding_balance"]) == Decimal("1000.00")
    assert body["open_invoice_count"] == 1


# ── Cross-matter scoping ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_portal_token_cannot_reach_another_matter(
    client, db_session, test_tenant, test_user, portal_matter, portal_invite
):
    """A token minted for matter A must not read matter B, same tenant or not."""
    other = Matter(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        slug=f"other-matter-{uuid.uuid4().hex[:8]}",
        matter_name="Unrelated Matter",
        matter_type="litigation",
        portal_enabled=True,
    )
    db_session.add(other)
    await db_session.commit()

    # The invite is bound to portal_matter, so a token claiming the other matter
    # fails the invite/matter consistency check rather than being honored.
    forged = create_matter_portal_token(
        tenant_id=str(test_tenant.id),
        matter_id=str(other.id),
        contact_id=None,
        email=portal_invite.email,
        invite_id=str(portal_invite.id),
    )
    resp = await client.get(f"{PORTAL}/session", headers=_portal_headers(forged))
    assert resp.status_code == 401


# ── Invite acceptance ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_accept_issues_a_scoped_cookie_and_records_acceptance(
    client, db_session, portal_invite, portal_matter
):
    resp = await client.post(
        f"{PORTAL}/accept",
        headers={"Authorization": ""},
        json={"token": portal_invite.raw_token},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["matter_name"] == portal_matter.matter_name
    assert CLIENT_PORTAL_COOKIE_NAME in resp.cookies

    await db_session.refresh(portal_invite)
    assert portal_invite.accepted_at is not None
    assert portal_invite.last_seen_at is not None

    # The cookie the accept handed back is a working session.
    session = await client.get(
        f"{PORTAL}/session",
        headers=_portal_headers(resp.cookies[CLIENT_PORTAL_COOKIE_NAME]),
    )
    assert session.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_token", ["not-a-real-token", ""])
async def test_accept_is_not_an_oracle_for_bad_tokens(client, portal_invite, bad_token):
    resp = await client.post(
        f"{PORTAL}/accept", headers={"Authorization": ""}, json={"token": bad_token}
    )
    # An unknown token and an empty one must not be distinguishable as
    # "wrong token" vs "malformed request" beyond basic shape validation.
    assert resp.status_code in (404, 422)


@pytest.mark.asyncio
async def test_accept_rejects_an_expired_invite(client, db_session, portal_invite):
    portal_invite.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
    await db_session.commit()
    resp = await client.post(
        f"{PORTAL}/accept",
        headers={"Authorization": ""},
        json={"token": portal_invite.raw_token},
    )
    assert resp.status_code == 404


# ── Document download ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_client_can_download_a_document_it_uploaded(client, portal_cookie):
    headers = _portal_headers(portal_cookie)
    upload = await client.post(
        f"{PORTAL}/documents/upload",
        headers=headers,
        files={"file": ("retainer.pdf", io.BytesIO(b"%PDF-1.4 body"), "application/pdf")},
    )
    assert upload.status_code == 201, upload.text
    doc_id = upload.json()["id"]

    resp = await client.get(f"{PORTAL}/documents/{doc_id}/download", headers=headers)
    assert resp.status_code == 200
    assert resp.content == b"%PDF-1.4 body"
    assert "retainer.pdf" in resp.headers["content-disposition"]
    # Privileged material must not be cached by intermediaries or sniffed.
    assert resp.headers["cache-control"] == "private, no-store"
    assert resp.headers["x-content-type-options"] == "nosniff"


@pytest.mark.asyncio
async def test_download_refuses_a_document_hidden_from_the_portal(
    client, db_session, portal_cookie
):
    from app.models.matter_document import MatterDocument

    headers = _portal_headers(portal_cookie)
    upload = await client.post(
        f"{PORTAL}/documents/upload",
        headers=headers,
        files={"file": ("notes.pdf", io.BytesIO(b"%PDF private"), "application/pdf")},
    )
    doc_id = upload.json()["id"]
    doc = await db_session.get(MatterDocument, uuid.UUID(doc_id))
    doc.portal_visible = False
    await db_session.commit()

    resp = await client.get(f"{PORTAL}/documents/{doc_id}/download", headers=headers)
    assert resp.status_code == 404
    assert (await client.get(f"{PORTAL}/documents", headers=headers)).json() == []


# ── Firm-side invite management ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_firm_invite_creation_enables_the_portal_and_returns_a_link(
    client, db_session, portal_matter
):
    portal_matter.portal_enabled = False
    await db_session.commit()

    resp = await client.post(
        f"/api/matters/{portal_matter.id}/portal/invite",
        json={"email": "newclient@example.com"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["email"] == "newclient@example.com"
    assert "/portal/client/accept?token=" in body["invite_url"]
    assert body["revoked"] is False

    await db_session.refresh(portal_matter)
    assert portal_matter.portal_enabled is True


@pytest.mark.asyncio
async def test_firm_invite_can_revoke_prior_live_invites(
    client, db_session, portal_matter, portal_invite
):
    resp = await client.post(
        f"/api/matters/{portal_matter.id}/portal/invite",
        json={"email": "replacement@example.com", "revoke_existing": True},
    )
    assert resp.status_code == 201, resp.text

    await db_session.refresh(portal_invite)
    assert portal_invite.revoked is True

    listing = await client.get(f"/api/matters/{portal_matter.id}/portal/invites")
    assert listing.status_code == 200
    rows = {r["email"]: r for r in listing.json()}
    assert rows["client@example.com"]["revoked"] is True
    assert rows["replacement@example.com"]["revoked"] is False
    # The raw token is handed back once, at creation, and never re-listed.
    assert rows["replacement@example.com"]["invite_url"] is None


@pytest.mark.asyncio
async def test_firm_invite_requires_an_email_when_no_client_contact_exists(
    client, portal_matter
):
    resp = await client.post(
        f"/api/matters/{portal_matter.id}/portal/invite", json={}
    )
    assert resp.status_code == 400
    assert "email is required" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_firm_revoke_reports_the_last_seen_activity(
    client, db_session, portal_matter, portal_invite, portal_cookie
):
    # A portal request stamps activity the firm can see.
    await client.get(f"{PORTAL}/session", headers=_portal_headers(portal_cookie))

    listing = await client.get(f"/api/matters/{portal_matter.id}/portal/invites")
    assert listing.json()[0]["last_seen_at"] is not None

    revoke = await client.delete(
        f"/api/matters/{portal_matter.id}/portal/invites/{portal_invite.id}"
    )
    assert revoke.status_code == 204

    listing = await client.get(f"/api/matters/{portal_matter.id}/portal/invites")
    assert listing.json()[0]["revoked"] is True


@pytest.mark.asyncio
async def test_firm_revoke_rejects_an_unknown_invite(client, portal_matter):
    resp = await client.delete(
        f"/api/matters/{portal_matter.id}/portal/invites/{uuid.uuid4()}"
    )
    assert resp.status_code == 404


# ── Pending signatures on the overview ──────────────────────────────────────


async def _add_signature_request(db_session, tenant, matter, user, signer_email, **kw):
    from app.models.signature import SignatureRequest, SignatureSigner

    request = SignatureRequest(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        matter_id=matter.id,
        status=kw.pop("status", "sent"),
        created_by_user_id=user.id,
        sent_at=datetime.now(timezone.utc),
        enforce_signing_order=kw.pop("enforce_signing_order", False),
    )
    db_session.add(request)
    db_session.add(
        SignatureSigner(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            request_id=request.id,
            name="Client Name",
            email=signer_email,
            role="client",
            status=kw.pop("signer_status", "pending"),
            sign_order=kw.pop("sign_order", 0),
        )
    )
    for extra in kw.pop("other_signers", []):
        db_session.add(
            SignatureSigner(
                id=uuid.uuid4(),
                tenant_id=tenant.id,
                request_id=request.id,
                name=extra.get("name", "Other Signer"),
                email=extra["email"],
                role=extra.get("role", "counterparty"),
                status=extra.get("status", "pending"),
                sign_order=extra.get("sign_order", 0),
            )
        )
    await db_session.commit()
    return request


@pytest.mark.asyncio
async def test_overview_counts_signatures_awaiting_this_client(
    client, db_session, test_tenant, test_user, portal_matter, portal_cookie
):
    await _add_signature_request(
        db_session, test_tenant, portal_matter, test_user, "CLIENT@example.com"
    )
    body = (
        await client.get(f"{PORTAL}/matter", headers=_portal_headers(portal_cookie))
    ).json()
    # The signer match is case-insensitive on email, as the e-signature side is.
    assert body["pending_signature_count"] == 1


@pytest.mark.asyncio
async def test_overview_ignores_signatures_for_a_different_signer(
    client, db_session, test_tenant, test_user, portal_matter, portal_cookie
):
    await _add_signature_request(
        db_session, test_tenant, portal_matter, test_user, "someone.else@example.com"
    )
    body = (
        await client.get(f"{PORTAL}/matter", headers=_portal_headers(portal_cookie))
    ).json()
    assert body["pending_signature_count"] == 0


@pytest.mark.asyncio
async def test_overview_ignores_signatures_this_client_already_signed(
    client, db_session, test_tenant, test_user, portal_matter, portal_cookie
):
    await _add_signature_request(
        db_session,
        test_tenant,
        portal_matter,
        test_user,
        "client@example.com",
        signer_status="signed",
    )
    body = (
        await client.get(f"{PORTAL}/matter", headers=_portal_headers(portal_cookie))
    ).json()
    assert body["pending_signature_count"] == 0


@pytest.mark.asyncio
async def test_logout_expires_the_cookie_in_the_browser(client, portal_cookie):
    """Sign-out must expire the cookie, not just blacklist the JTI.

    The blacklist is the second line of defence: without Redis it is per-worker,
    and with Redis it lapses when the entry is evicted or the server restarts.
    If the browser keeps the cookie, either of those silently restores a session
    the client believed they had ended.
    """
    resp = await client.post(f"{PORTAL}/logout", headers=_portal_headers(portal_cookie))
    assert resp.status_code == 204

    set_cookie = resp.headers.get("set-cookie", "")
    assert CLIENT_PORTAL_COOKIE_NAME in set_cookie
    # An immediate expiry is what actually removes it from the browser's jar.
    assert 'Max-Age=0' in set_cookie or "Expires=Thu, 01 Jan 1970" in set_cookie


@pytest.mark.asyncio
async def test_overview_ignores_signatures_blocked_by_signing_order(
    client, db_session, test_tenant, test_user, portal_matter, portal_cookie
):
    """The badge must agree with what the Signatures tab will actually show.

    With an enforced signing order and the client second, the e-signature list
    endpoint filters the request out. Counting it here would badge the tab for
    a client who opens it and finds nothing to do.
    """
    await _add_signature_request(
        db_session,
        test_tenant,
        portal_matter,
        test_user,
        "client@example.com",
        enforce_signing_order=True,
        sign_order=1,
        other_signers=[{"email": "first@firm.com", "sign_order": 0}],
    )
    body = (
        await client.get(f"{PORTAL}/matter", headers=_portal_headers(portal_cookie))
    ).json()
    assert body["pending_signature_count"] == 0


@pytest.mark.asyncio
async def test_overview_counts_a_signature_once_the_client_turn_arrives(
    client, db_session, test_tenant, test_user, portal_matter, portal_cookie
):
    await _add_signature_request(
        db_session,
        test_tenant,
        portal_matter,
        test_user,
        "client@example.com",
        status="partially_signed",
        enforce_signing_order=True,
        sign_order=1,
        other_signers=[
            {"email": "first@firm.com", "sign_order": 0, "status": "signed"}
        ],
    )
    body = (
        await client.get(f"{PORTAL}/matter", headers=_portal_headers(portal_cookie))
    ).json()
    assert body["pending_signature_count"] == 1


# ── Read receipts ───────────────────────────────────────────────────────────


async def _add_firm_message(db_session, tenant, matter, subject, occurred_at):
    db_session.add(
        CommunicationLog(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            matter_id=matter.id,
            direction="outbound",
            channel="portal",
            status="sent",
            subject=subject,
            body=subject,
            occurred_at=occurred_at,
        )
    )
    await db_session.commit()


@pytest.mark.asyncio
async def test_read_receipt_does_not_swallow_a_message_the_client_never_saw(
    client, db_session, test_tenant, portal_matter, portal_cookie
):
    """A message that arrives between the list and the receipt stays unread.

    Marking read at server ``now`` would clear the badge for correspondence the
    client was never shown — the worst failure mode for a legal portal, because
    nothing later flags it as new.
    """
    headers = _portal_headers(portal_cookie)
    seen = datetime.now(timezone.utc) - timedelta(minutes=5)
    await _add_firm_message(db_session, test_tenant, portal_matter, "Delivered", seen)

    # Arrives after the client's list request, before their read receipt.
    await _add_firm_message(
        db_session,
        test_tenant,
        portal_matter,
        "Arrived mid-flight",
        datetime.now(timezone.utc) - timedelta(minutes=1),
    )

    resp = await client.post(
        f"{PORTAL}/messages/read",
        headers=headers,
        json={"seen_through": seen.isoformat()},
    )
    assert resp.status_code == 200
    assert resp.json()["unread_count"] == 1

    listing = (await client.get(f"{PORTAL}/messages", headers=headers)).json()
    unread = [m["subject"] for m in listing["messages"] if m["unread"]]
    assert unread == ["Arrived mid-flight"]


@pytest.mark.asyncio
async def test_read_receipt_falls_back_to_now_without_a_bound(
    client, db_session, test_tenant, portal_matter, portal_cookie
):
    headers = _portal_headers(portal_cookie)
    await _add_firm_message(
        db_session,
        test_tenant,
        portal_matter,
        "Anything",
        datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    resp = await client.post(f"{PORTAL}/messages/read", headers=headers, json={})
    assert resp.status_code == 200
    assert resp.json()["unread_count"] == 0


@pytest.mark.asyncio
async def test_read_receipt_never_moves_the_mark_backwards(
    client, db_session, test_tenant, portal_matter, portal_cookie
):
    """A stale tab posting an old receipt must not un-read newer messages."""
    headers = _portal_headers(portal_cookie)
    await _add_firm_message(
        db_session,
        test_tenant,
        portal_matter,
        "Old news",
        datetime.now(timezone.utc) - timedelta(hours=2),
    )
    assert (await client.post(f"{PORTAL}/messages/read", headers=headers, json={})).json()[
        "unread_count"
    ] == 0

    stale = datetime.now(timezone.utc) - timedelta(days=1)
    resp = await client.post(
        f"{PORTAL}/messages/read", headers=headers, json={"seen_through": stale.isoformat()}
    )
    assert resp.status_code == 200
    assert resp.json()["unread_count"] == 0


@pytest.mark.asyncio
async def test_read_receipt_cannot_be_dated_into_the_future(
    client, db_session, test_tenant, portal_matter, portal_cookie
):
    """A future bound must not pre-read messages that have not arrived yet."""
    headers = _portal_headers(portal_cookie)
    future = datetime.now(timezone.utc) + timedelta(days=7)
    resp = await client.post(
        f"{PORTAL}/messages/read",
        headers=headers,
        json={"seen_through": future.isoformat()},
    )
    assert resp.status_code == 200
    assert datetime.fromisoformat(resp.json()["messages_seen_at"]) <= datetime.now(
        timezone.utc
    ) + timedelta(seconds=5)

    await _add_firm_message(
        db_session, test_tenant, portal_matter, "After the receipt", datetime.now(timezone.utc)
    )
    body = (await client.get(f"{PORTAL}/matter", headers=headers)).json()
    assert body["unread_message_count"] == 1
