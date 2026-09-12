import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

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


# ── Sign-in code lifetime, the matter-list cache, and the chooser ───────────


class _FakeRedis:
    """Records every write so a test can see the TTL each store was given."""

    def __init__(self):
        self.store: dict[str, tuple[str, int]] = {}
        self.setex_calls: list[tuple[str, int]] = []
        self.deleted: list[str] = []

    async def get(self, key):
        entry = self.store.get(key)
        return entry[0] if entry else None

    async def setex(self, key, ttl, value):
        self.setex_calls.append((key, ttl))
        self.store[key] = (value, ttl)

    async def delete(self, key):
        self.deleted.append(key)
        self.store.pop(key, None)

    async def ttl(self, key):
        entry = self.store.get(key)
        return entry[1] if entry else -2

    async def exists(self, key):
        return key in self.store


def _redis_request(redis, token=None):
    request = SimpleNamespace(
        cookies={"client_portal_token": token} if token else {},
        headers={},
        app=SimpleNamespace(state=SimpleNamespace(redis=redis)),
    )
    return request


async def _stored_code(request, email, code):
    key = client_portal._signin_code_key(email)
    await client_portal._store_json(
        request,
        key,
        {"code_hash": client_portal._hash_signin_code(email, code), "attempts": 0},
        settings.PORTAL_SIGNIN_CODE_TTL_SECONDS,
        client_portal._signin_codes_fallback,
    )
    return key


async def _verify(request, email, code):
    from fastapi import Response

    return await client_portal.verify_portal_signin_code(
        client_portal.ClientPortalVerifyCodeRequest(email=email, code=code),
        Response(),
        request,
        db=None,
    )


@pytest.mark.asyncio
async def test_wrong_sign_in_attempt_keeps_the_codes_remaining_life():
    redis = _FakeRedis()
    request = _redis_request(redis)
    email = "client@example.com"
    key = await _stored_code(request, email, "123456")
    # Time passes: the code has 200 of its 600 seconds left.
    value, _ttl = redis.store[key]
    redis.store[key] = (value, 200)

    with pytest.raises(HTTPException) as exc:
        await _verify(request, email, "000000")

    assert exc.value.status_code == 400
    key_written, ttl_written = redis.setex_calls[-1]
    assert key_written == key
    assert ttl_written == 200
    assert ttl_written < settings.PORTAL_SIGNIN_CODE_TTL_SECONDS
    assert json.loads(redis.store[key][0])["attempts"] == 1


@pytest.mark.asyncio
async def test_sign_in_code_attempt_cap_is_enforced(monkeypatch):
    # A verified code with no reachable matter answers 403, which is how the
    # test tells "accepted" from "refused" (400) without a database.
    monkeypatch.setattr(
        client_portal, "_client_portal_matches", AsyncMock(return_value=[])
    )
    redis = _FakeRedis()
    request = _redis_request(redis)
    cap = settings.PORTAL_SIGNIN_CODE_ATTEMPTS

    spared = "spared@example.com"
    key = await _stored_code(request, spared, "123456")
    for _ in range(cap - 1):
        with pytest.raises(HTTPException) as exc:
            await _verify(request, spared, "000000")
        assert exc.value.status_code == 400
    assert key in redis.store
    with pytest.raises(HTTPException) as exc:
        await _verify(request, spared, "123456")
    assert exc.value.status_code == 403

    capped = "capped@example.com"
    key = await _stored_code(request, capped, "123456")
    for _ in range(cap):
        with pytest.raises(HTTPException) as exc:
            await _verify(request, capped, "000000")
        assert exc.value.status_code == 400
    assert key not in redis.store
    with pytest.raises(HTTPException) as exc:
        await _verify(request, capped, "123456")
    assert exc.value.status_code == 400


def _portal_ctx(email="Client@Example.com"):
    return client_portal.ClientPortalContext(
        tenant_id=str(uuid.uuid4()),
        matter_id=str(uuid.uuid4()),
        contact_id=None,
        email=email,
        invite_id=str(uuid.uuid4()),
    )


@pytest.mark.asyncio
async def test_matter_list_is_served_from_cache_until_sign_out(monkeypatch):
    from fastapi import Response

    lookup = AsyncMock(return_value=[object()])
    choice = client_portal.PortalMatterChoice(
        matter_id=str(uuid.uuid4()), matter_name="Smith v. Jones", firm_name="Firm"
    )
    monkeypatch.setattr(client_portal, "_client_portal_matches", lookup)
    monkeypatch.setattr(client_portal, "_matter_choice", AsyncMock(return_value=choice))
    redis = _FakeRedis()
    request = _redis_request(redis)
    ctx = _portal_ctx()
    resolved = (ctx, SimpleNamespace(id=ctx.matter_id))

    first = await client_portal.portal_list_matters(request, resolved, db=None)
    second = await client_portal.portal_list_matters(request, resolved, db=None)

    assert [row.model_dump() for row in first] == [choice.model_dump()]
    assert [row.model_dump() for row in second] == [choice.model_dump()]
    # The cross-tenant lookup ran once; the second page load read the cache.
    assert lookup.await_count == 1
    cache_key = client_portal._matters_cache_key("client@example.com")
    assert redis.setex_calls[-1] == (
        cache_key,
        client_portal.PORTAL_MATTERS_CACHE_TTL_SECONDS,
    )

    token = create_matter_portal_token(
        tenant_id=ctx.tenant_id,
        matter_id=ctx.matter_id,
        contact_id=None,
        email=ctx.email,
        invite_id=ctx.invite_id,
    )
    await client_portal.portal_logout(_redis_request(redis, token), Response())
    assert cache_key in redis.deleted

    await client_portal.portal_list_matters(request, resolved, db=None)
    assert lookup.await_count == 2


@pytest.mark.asyncio
async def test_switching_matter_forgets_the_cached_list(monkeypatch):
    from fastapi import Response

    monkeypatch.setattr(
        client_portal, "_client_portal_matches", AsyncMock(return_value=[])
    )
    redis = _FakeRedis()
    request = _redis_request(redis)
    ctx = _portal_ctx()
    cache_key = client_portal._matters_cache_key("client@example.com")
    redis.store[cache_key] = ('{"matters": []}', 100)

    with pytest.raises(HTTPException) as exc:
        await client_portal.switch_portal_matter(
            client_portal.ClientPortalSwitchMatterRequest(matter_id=str(uuid.uuid4())),
            request,
            Response(),
            (ctx, SimpleNamespace(id=ctx.matter_id)),
            db=None,
        )

    assert exc.value.status_code == 403
    assert cache_key in redis.deleted


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "branded_name,expected",
    [("Northline Legal LLP", "Northline Legal LLP"), (None, "Tenant Co")],
)
async def test_matter_chooser_names_the_firm_like_the_portal_header(
    monkeypatch, branded_name, expected
):
    tenant = SimpleNamespace(name="Tenant Co")
    db = SimpleNamespace(get=AsyncMock(return_value=tenant))
    monkeypatch.setattr(client_portal, "set_tenant_context", AsyncMock())
    monkeypatch.setattr(
        client_portal,
        "get_firm_branding",
        AsyncMock(return_value={"firm_name": branded_name}),
    )
    match = SimpleNamespace(
        tenant_id=str(uuid.uuid4()),
        matter=SimpleNamespace(
            id=uuid.uuid4(), matter_name="Smith v. Jones", matter_number="SMI0001"
        ),
    )

    choice = await client_portal._matter_choice(db, match)

    assert choice.firm_name == expected
    assert choice.matter_number == "SMI0001"
