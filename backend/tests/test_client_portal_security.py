import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from jose import jwt

from app.config import get_settings
from app.models.client_portal import ClientPortalInvite
from app.models.signature import SignatureSigner
from app.routers import client_portal
from app.routers.esignature import _portal_signer_matches_context
from app.services.portal_token import create_matter_portal_token

settings = get_settings()


class _FakeResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _FakeSession:
    def __init__(self, invite, *, tenant_active=True):
        self.invite = invite
        self.tenant_active = tenant_active
        self.committed = False

    async def scalar(self, stmt):
        return SimpleNamespace(is_active=True) if self.tenant_active else None

    async def execute(self, stmt):
        return _FakeResult(self.invite)

    async def commit(self):
        self.committed = True

    async def rollback(self):
        pass


class _FakeRequest:
    def __init__(self, token):
        self.cookies = {"client_portal_token": token}
        self.headers = {}
        self.app = SimpleNamespace(state=SimpleNamespace(redis=None, jti_blacklist={}))


@pytest.mark.asyncio
async def test_client_portal_context_rejects_revoked_invite(monkeypatch):
    tenant_id = uuid.uuid4()
    matter_id = uuid.uuid4()
    invite_id = uuid.uuid4()
    token = create_matter_portal_token(
        tenant_id=str(tenant_id),
        matter_id=str(matter_id),
        contact_id=None,
        email="client@example.com",
        invite_id=str(invite_id),
    )
    invite = ClientPortalInvite(
        id=invite_id,
        tenant_id=tenant_id,
        matter_id=matter_id,
        token_hash="x" * 64,
        email="client@example.com",
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        revoked=True,
    )

    async def noop_bind_tenant_context(db, tenant):
        return None

    monkeypatch.setattr(client_portal, "bind_tenant_context", noop_bind_tenant_context)

    with pytest.raises(HTTPException) as exc:
        await client_portal.get_client_portal_context(
            _FakeRequest(token), _FakeSession(invite)
        )

    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_client_portal_context_rejects_inactive_tenant(monkeypatch):
    tenant_id = uuid.uuid4()
    matter_id = uuid.uuid4()
    invite_id = uuid.uuid4()
    token = create_matter_portal_token(
        tenant_id=str(tenant_id),
        matter_id=str(matter_id),
        contact_id=None,
        email="client@example.com",
        invite_id=str(invite_id),
    )
    invite = ClientPortalInvite(
        id=invite_id,
        tenant_id=tenant_id,
        matter_id=matter_id,
        token_hash="x" * 64,
        email="client@example.com",
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        revoked=False,
    )

    async def noop_bind_tenant_context(db, tenant):
        return None

    monkeypatch.setattr(client_portal, "bind_tenant_context", noop_bind_tenant_context)

    with pytest.raises(HTTPException) as exc:
        await client_portal.get_client_portal_context(
            _FakeRequest(token), _FakeSession(invite, tenant_active=False)
        )

    assert exc.value.status_code == 401
    assert exc.value.detail == "Portal session unavailable"


def test_matter_portal_token_uses_invite_id_and_separate_cookie_name():
    token = create_matter_portal_token(
        tenant_id=str(uuid.uuid4()),
        matter_id=str(uuid.uuid4()),
        contact_id=None,
        email="client@example.com",
        invite_id=str(uuid.uuid4()),
    )
    payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])

    assert payload["invite_id"]
    assert client_portal.CLIENT_PORTAL_COOKIE_NAME == "client_portal_token"


@pytest.mark.asyncio
async def test_client_portal_context_rejects_legacy_token_without_invite_id(
    monkeypatch,
):
    token = jwt.encode(
        {
            "client_portal": True,
            "tenant_id": str(uuid.uuid4()),
            "matter_id": str(uuid.uuid4()),
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        },
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )

    async def noop_bind_tenant_context(db, tenant):
        return None

    monkeypatch.setattr(client_portal, "bind_tenant_context", noop_bind_tenant_context)

    with pytest.raises(HTTPException) as exc:
        await client_portal.get_client_portal_context(
            _FakeRequest(token), _FakeSession(None)
        )

    assert exc.value.status_code == 401


def test_portal_signer_matching_requires_same_contact_or_email():
    contact_id = uuid.uuid4()
    matching_contact = SignatureSigner(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        request_id=uuid.uuid4(),
        contact_id=contact_id,
        name="Client Signer",
        email="other@example.com",
    )
    matching_email = SignatureSigner(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        request_id=uuid.uuid4(),
        contact_id=None,
        name="Client Signer",
        email="CLIENT@EXAMPLE.COM",
    )
    wrong_signer = SignatureSigner(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        request_id=uuid.uuid4(),
        contact_id=uuid.uuid4(),
        name="Other Client",
        email="other@example.com",
    )
    ctx = client_portal.ClientPortalContext(
        tenant_id=str(uuid.uuid4()),
        matter_id=str(uuid.uuid4()),
        contact_id=str(contact_id),
        email="client@example.com",
        invite_id=str(uuid.uuid4()),
    )

    assert _portal_signer_matches_context(matching_contact, ctx)
    assert _portal_signer_matches_context(matching_email, ctx)
    assert not _portal_signer_matches_context(wrong_signer, ctx)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,allowed",
    [
        ("matter", True),
        ("signatures", True),
        ("intake", True),
        ("intake/questionnaire", True),
        ("documents/upload", True),
        ("documents", False),
        ("messages", False),
        ("billing", False),
        ("signatures/foreign/sign", False),
        ("documents/foreign/download", False),
    ],
)
async def test_paperwork_link_restricts_general_portal_until_fee_signed(
    monkeypatch, path, allowed
):
    from unittest.mock import AsyncMock
    from app.models.matter_intake import MatterIntake
    from app.models.tenant import Tenant
    from app.models.signature import SignatureRequest

    tid, mid, iid, cid, sid, did = [uuid.uuid4() for _ in range(6)]
    invite = ClientPortalInvite(
        id=iid,
        tenant_id=tid,
        matter_id=mid,
        contact_id=cid,
        email="jane@example.com",
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        revoked=False,
    )
    packet = SimpleNamespace(
        signature_id=sid,
        config={"portal_after_signing": True},
        requirements={
            "fee_agreement": {"completed": False},
            "document_extra": {"signature_id": str(sid), "document_id": str(did)},
        },
        status="awaiting_documents",
    )

    async def scalar(stmt):
        model = stmt.column_descriptions[0]["entity"]
        return {
            Tenant: SimpleNamespace(is_active=True),
            MatterIntake: packet,
            SignatureRequest: did,
        }[model]

    db = SimpleNamespace(
        scalar=scalar, execute=AsyncMock(return_value=_FakeResult(invite))
    )
    monkeypatch.setattr(client_portal, "bind_tenant_context", AsyncMock())
    monkeypatch.setattr(client_portal, "_touch_last_seen", AsyncMock())
    token = create_matter_portal_token(
        tenant_id=str(tid),
        matter_id=str(mid),
        contact_id=str(cid),
        email=invite.email,
        invite_id=str(iid),
    )
    request = _FakeRequest(token)
    request.url = SimpleNamespace(path="/api/portal/client/" + path)
    if allowed:
        ctx = await client_portal.get_client_portal_context(request, db)
        assert ctx.paperwork_only and str(sid) in ctx.paperwork_signature_ids
    else:
        with pytest.raises(HTTPException) as denied:
            await client_portal.get_client_portal_context(request, db)
        assert denied.value.status_code == 403
    packet.requirements["fee_agreement"]["completed"] = True
    ctx = await client_portal.get_client_portal_context(request, db)
    assert not ctx.paperwork_only
