from types import SimpleNamespace
import uuid

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import app.routers.esignature as router
from app.models.signature import SignatureRequest, SignatureSigner


def _request(status="sent"):
    row = SignatureRequest(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        matter_id=uuid.uuid4(),
        status=status,
        provider="internal",
    )
    row.signers = [
        SignatureSigner(
            id=uuid.uuid4(),
            name="Client",
            email="client@example.com",
            status="pending",
            sign_order=0,
        )
    ]
    return row


class _Db:
    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1


def _http_request():
    return Request({"type": "http", "headers": [], "client": ("127.0.0.1", 1)})


@pytest.mark.asyncio
async def test_resend_open_request_notifies_actionable_signers(monkeypatch):
    row = _request()
    db = _Db()
    user = SimpleNamespace(tenant_id=row.tenant_id)
    notified = []

    async def load(*args):
        return row

    async def notify(req):
        notified.append(req)

    async def response(_db, req):
        return req

    async def no_op(*args):
        return None

    async def current_user(*args):
        return user

    monkeypatch.setattr(router, "get_current_user", current_user)
    monkeypatch.setattr(router, "set_tenant_context", no_op)
    monkeypatch.setattr(router, "_load_request", load)
    monkeypatch.setattr(router, "_expire_and_commit_if_needed", no_op)
    monkeypatch.setattr(router, "notify_actionable_signers", notify)
    monkeypatch.setattr(router, "_to_response", response)

    result = await router.resend_signature_request(
        str(row.matter_id), str(row.id), _http_request(), db
    )
    assert result is row
    assert notified == [row]
    assert db.commits == 1


@pytest.mark.asyncio
async def test_resend_rejects_closed_request(monkeypatch):
    row = _request("completed")

    async def load(*args):
        return row

    async def no_op(*args):
        return None

    async def current_user(*args):
        return SimpleNamespace(tenant_id=row.tenant_id)

    monkeypatch.setattr(router, "get_current_user", current_user)
    monkeypatch.setattr(router, "set_tenant_context", no_op)
    monkeypatch.setattr(router, "_load_request", load)
    monkeypatch.setattr(router, "_expire_and_commit_if_needed", no_op)
    with pytest.raises(HTTPException) as exc:
        await router.resend_signature_request(
            str(row.matter_id), str(row.id), _http_request(), _Db()
        )
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_send_dispatches_provider_and_signer_invitation(monkeypatch):
    row = _request("draft")
    db = _Db()
    notified = []

    class Provider:
        async def send(self, request):
            return "envelope-1"

    async def current_user(*args):
        return SimpleNamespace(tenant_id=row.tenant_id)

    async def load(*args):
        return row

    async def no_op(*args):
        return None

    async def unchanged(*args):
        return True

    async def notify(request):
        notified.append(request)

    async def response(_db, request):
        return request

    monkeypatch.setattr(router, "get_current_user", current_user)
    monkeypatch.setattr(router, "set_tenant_context", no_op)
    monkeypatch.setattr(router, "_load_request", load)
    monkeypatch.setattr(router, "_expire_and_commit_if_needed", no_op)
    monkeypatch.setattr(router, "_source_document_is_unchanged", unchanged)
    monkeypatch.setattr(router, "get_provider", lambda name: Provider())
    monkeypatch.setattr(router, "notify_actionable_signers", notify)
    monkeypatch.setattr(router, "_to_response", response)

    result = await router.send_signature_request(
        str(row.matter_id), str(row.id), _http_request(), db
    )
    assert result.status == "sent"
    assert result.provider_envelope_id == "envelope-1"
    assert notified == [row]
    assert db.commits == 1
