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

    async def scalar(self, stmt):
        return SimpleNamespace(is_active=True) if self.tenant_active else None

    async def execute(self, stmt):
        return _FakeResult(self.invite)


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

    async def noop_set_tenant_context(db, tenant):
        return None

    monkeypatch.setattr(client_portal, "set_tenant_context", noop_set_tenant_context)

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

    async def noop_set_tenant_context(db, tenant):
        return None

    monkeypatch.setattr(client_portal, "set_tenant_context", noop_set_tenant_context)

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

    async def noop_set_tenant_context(db, tenant):
        return None

    monkeypatch.setattr(client_portal, "set_tenant_context", noop_set_tenant_context)

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
