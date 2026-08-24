import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.models.inbound_email import InboundEmail, InboundEmailAlias
from app.routers import matters_correspondence as routes


class FakeResult:
    def __init__(self, value=None, rows=None):
        self.value = value
        self.rows = list(rows or [])

    def scalar_one_or_none(self):
        return self.value

    def scalar_one(self):
        return self.value

    def scalars(self):
        return self

    def all(self):
        return self.rows


class FakeDB:
    def __init__(self, *results, commit_error=None):
        self.results = list(results)
        self.executed = []
        self.added = []
        self.commits = 0
        self.rollbacks = 0
        self.flushes = 0
        self.commit_error = commit_error

    async def execute(self, statement, params=None):
        self.executed.append((statement, params))
        if not self.results:
            raise AssertionError("Unexpected database execute")
        return self.results.pop(0)

    def add(self, item):
        self.added.append(item)

    async def commit(self):
        self.commits += 1
        if self.commit_error is not None:
            error = self.commit_error
            self.commit_error = None
            raise error

    async def rollback(self):
        self.rollbacks += 1

    async def flush(self):
        self.flushes += 1

    async def refresh(self, item):
        if getattr(item, "id", None) is None:
            item.id = uuid.uuid4()
        if getattr(item, "created_at", None) is None:
            item.created_at = datetime.now(timezone.utc)


class StreamRequest:
    def __init__(self, headers=None, chunks=None):
        self.headers = headers or {}
        self._chunks = list(chunks or [])

    async def stream(self):
        for chunk in self._chunks:
            yield chunk


def user_and_matter():
    tenant_id = uuid.uuid4()
    user = SimpleNamespace(id=uuid.uuid4(), tenant_id=tenant_id)
    matter = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        slug="sample-matter",
        cloud_folder=None,
        correspondence_rules=None,
        case_number=None,
    )
    return user, matter


def alias_row(user, matter):
    return SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        matter_id=matter.id,
        status="active",
        encrypted_local_part="encrypted",
        last_received_at=None,
        created_at=datetime.now(timezone.utc),
        revoked_at=None,
    )


def inbound_row(user, matter, alias):
    now = datetime.now(timezone.utc)
    return InboundEmail(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        alias_id=alias.id,
        matter_id=matter.id,
        status="pending",
        envelope_sender="jane@example.com",
        recipient="m-abcdefghijklmnopqrstuvwxyz@intake.getlawhand.com",
        subject="Review this",
        body_preview="Body",
        participants={"from": "jane@example.com", "to": []},
        authentication_results={"authentication_results": ["dmarc=pass"]},
        provider_message_id="<id@example.com>",
        message_sha256="a" * 64,
        raw_storage_path="placeholder.eml",
        raw_size=12,
        occurred_at=now,
        created_at=now,
    )


@pytest.mark.asyncio
async def test_read_raw_body_enforces_declared_and_streamed_limits():
    request = StreamRequest({"content-length": "4"}, [b"ab", b"cd"])
    assert await routes._read_raw_body_capped(request, 4) == b"abcd"

    with pytest.raises(HTTPException) as exc:
        await routes._read_raw_body_capped(
            StreamRequest({"content-length": "5"}, [b"abcde"]), 4
        )
    assert exc.value.status_code == 413

    with pytest.raises(HTTPException) as exc:
        await routes._read_raw_body_capped(
            StreamRequest({"content-length": "nope"}, [b"x"]), 4
        )
    assert exc.value.status_code == 400

    with pytest.raises(HTTPException) as exc:
        await routes._read_raw_body_capped(StreamRequest({}, [b"abc", b"de"]), 4)
    assert exc.value.status_code == 413

    with pytest.raises(HTTPException) as exc:
        await routes._read_raw_body_capped(StreamRequest({}, []), 4)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_matter_and_queue_lookup_helpers_enforce_not_found_and_pending():
    user, matter = user_and_matter()
    found = await routes._get_matter_or_404(
        str(matter.id), user.tenant_id, FakeDB(FakeResult(matter)), for_update=True
    )
    assert found is matter

    with pytest.raises(HTTPException) as exc:
        await routes._get_matter_or_404(
            str(matter.id), user.tenant_id, FakeDB(FakeResult(None))
        )
    assert exc.value.status_code == 404

    item = SimpleNamespace(status="pending")
    assert (
        await routes._pending_inbound_or_404(
            FakeDB(FakeResult(item)),
            tenant_id=user.tenant_id,
            matter_id=matter.id,
            inbound_id=uuid.uuid4(),
        )
        is item
    )
    with pytest.raises(HTTPException) as exc:
        await routes._pending_inbound_or_404(
            FakeDB(FakeResult(None)),
            tenant_id=user.tenant_id,
            matter_id=matter.id,
            inbound_id=uuid.uuid4(),
        )
    assert exc.value.status_code == 404

    with pytest.raises(HTTPException) as exc:
        await routes._pending_inbound_or_404(
            FakeDB(FakeResult(SimpleNamespace(status="accepted"))),
            tenant_id=user.tenant_id,
            matter_id=matter.id,
            inbound_id=uuid.uuid4(),
        )
    assert exc.value.status_code == 409


def configure_ingress(monkeypatch, tmp_path, *, signature_valid=True):
    monkeypatch.setattr(routes.settings, "INBOUND_EMAIL_ENABLED", True)
    monkeypatch.setattr(routes.settings, "INBOUND_EMAIL_MAX_BYTES", 1024)
    monkeypatch.setattr(routes.settings, "INBOUND_EMAIL_WEBHOOK_SECRET", "x" * 64)
    monkeypatch.setattr(
        routes.settings, "INBOUND_EMAIL_SIGNATURE_TOLERANCE_SECONDS", 300
    )
    monkeypatch.setattr(
        routes.settings, "INBOUND_EMAIL_DOMAIN", "intake.getlawhand.com"
    )
    monkeypatch.setattr(
        routes, "verify_delivery_signature", lambda **_: signature_valid
    )
    monkeypatch.setattr(
        routes,
        "parse_raw_email",
        lambda _: {
            "subject": "Subject",
            "body_preview": "Preview",
            "participants": {"from": "jane@example.com", "to": []},
            "authentication_results": {"authentication_results": ["dmarc=pass"]},
            "message_id": "<id@example.com>",
            "occurred_at": datetime.now(timezone.utc),
        },
    )
    target = tmp_path / "inbound.eml"
    monkeypatch.setattr(routes, "quarantine_path", lambda *_: target)
    monkeypatch.setattr(
        routes, "write_quarantined_message", lambda path, raw: path.write_bytes(raw)
    )
    monkeypatch.setattr(routes, "set_inbound_email_route_lookup", AsyncMock())
    monkeypatch.setattr(routes, "set_tenant_context", AsyncMock())
    return target


def ingress_request(raw=b"raw message"):
    return StreamRequest(
        {
            "x-lawhand-envelope-from": "jane@example.com",
            "x-lawhand-envelope-to": "m-abcdefghijklmnopqrstuvwxyz@intake.getlawhand.com",
            "x-lawhand-timestamp": "1787580000",
            "x-lawhand-signature": "v1=test",
            "content-length": str(len(raw)),
        },
        [raw],
    )


@pytest.mark.asyncio
async def test_inbound_ingress_rejects_disabled_bad_headers_and_signature(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(routes.settings, "INBOUND_EMAIL_ENABLED", False)
    with pytest.raises(HTTPException) as exc:
        await routes.receive_cloudflare_inbound_email(ingress_request(), FakeDB())
    assert exc.value.status_code == 503

    configure_ingress(monkeypatch, tmp_path)
    request = ingress_request()
    request.headers["x-lawhand-envelope-from"] = "bad\nheader"
    with pytest.raises(HTTPException) as exc:
        await routes.receive_cloudflare_inbound_email(request, FakeDB())
    assert exc.value.status_code == 400

    request = ingress_request()
    request.headers["x-lawhand-envelope-from"] = "x" * 321
    with pytest.raises(HTTPException) as exc:
        await routes.receive_cloudflare_inbound_email(request, FakeDB())
    assert exc.value.status_code == 400

    configure_ingress(monkeypatch, tmp_path, signature_valid=False)
    with pytest.raises(HTTPException) as exc:
        await routes.receive_cloudflare_inbound_email(ingress_request(), FakeDB())
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_inbound_ingress_hides_unknown_and_duplicate_aliases(
    monkeypatch, tmp_path
):
    configure_ingress(monkeypatch, tmp_path)
    request = ingress_request()
    request.headers["x-lawhand-envelope-to"] = "not-an-alias@intake.getlawhand.com"
    assert await routes.receive_cloudflare_inbound_email(request, FakeDB()) == {
        "accepted": True
    }

    assert await routes.receive_cloudflare_inbound_email(
        ingress_request(), FakeDB(FakeResult(None))
    ) == {"accepted": True}

    user, matter = user_and_matter()
    alias = alias_row(user, matter)
    db = FakeDB(FakeResult(alias), FakeResult(matter), FakeResult(uuid.uuid4()))
    assert await routes.receive_cloudflare_inbound_email(ingress_request(), db) == {
        "accepted": True
    }
    assert not db.added


@pytest.mark.asyncio
async def test_inbound_ingress_quarantines_valid_mail_and_cleans_up_conflict(
    monkeypatch, tmp_path
):
    target = configure_ingress(monkeypatch, tmp_path)
    user, matter = user_and_matter()
    alias = alias_row(user, matter)
    db = FakeDB(FakeResult(alias), FakeResult(matter), FakeResult(None))

    assert await routes.receive_cloudflare_inbound_email(ingress_request(), db) == {
        "accepted": True
    }
    assert target.read_bytes() == b"raw message"
    assert isinstance(db.added[0], InboundEmail)
    assert db.commits == 1
    assert alias.last_received_at is not None

    target.unlink()
    db = FakeDB(
        FakeResult(alias),
        FakeResult(matter),
        FakeResult(None),
        commit_error=IntegrityError("insert", {}, RuntimeError("duplicate")),
    )
    assert await routes.receive_cloudflare_inbound_email(ingress_request(), db) == {
        "accepted": True
    }
    assert db.rollbacks == 1
    assert not target.exists()


@pytest.mark.asyncio
async def test_alias_lifecycle_and_queue_review_routes(monkeypatch):
    user, matter = user_and_matter()
    existing = alias_row(user, matter)
    request = object()

    monkeypatch.setattr(routes, "get_current_user", AsyncMock(return_value=user))
    monkeypatch.setattr(routes, "set_tenant_context", AsyncMock())
    monkeypatch.setattr(routes, "_get_matter_or_404", AsyncMock(return_value=matter))
    monkeypatch.setattr(
        routes, "decrypt_token", lambda _: "m-abcdefghijklmnopqrstuvwxyz"
    )
    monkeypatch.setattr(routes, "encrypt_token", lambda _: "encrypted")
    monkeypatch.setattr(
        routes, "generate_alias_local_part", lambda: "m-bcdefghijklmnopqrstuvwxyza"
    )
    monkeypatch.setattr(routes.settings, "INBOUND_EMAIL_ENABLED", True)
    monkeypatch.setattr(
        routes.settings, "INBOUND_EMAIL_DOMAIN", "intake.getlawhand.com"
    )

    monkeypatch.setattr(routes, "_active_alias", AsyncMock(return_value=existing))
    response = await routes.get_matter_inbound_alias(str(matter.id), request, FakeDB())
    assert response.alias.address.endswith("@intake.getlawhand.com")

    response = await routes.create_matter_inbound_alias(
        str(matter.id), request, FakeDB()
    )
    assert response.alias.id == existing.id

    monkeypatch.setattr(routes, "_active_alias", AsyncMock(return_value=None))
    create_db = FakeDB()
    response = await routes.create_matter_inbound_alias(
        str(matter.id), request, create_db
    )
    assert response.alias.address.startswith("m-")
    assert isinstance(create_db.added[0], InboundEmailAlias)

    monkeypatch.setattr(routes, "_active_alias", AsyncMock(return_value=existing))
    rotate_db = FakeDB()
    response = await routes.rotate_matter_inbound_alias(
        str(matter.id), request, rotate_db
    )
    assert existing.status == "revoked"
    assert response.alias.status == "active"

    existing.status = "active"
    disable_db = FakeDB()
    await routes.disable_matter_inbound_alias(str(matter.id), request, disable_db)
    assert existing.status == "revoked"
    assert disable_db.commits == 1

    item = inbound_row(user, matter, existing)
    list_db = FakeDB(FakeResult(1), FakeResult(rows=[item]))
    listed = await routes.list_matter_inbound_email(
        str(matter.id), request, "pending", list_db
    )
    assert listed.total == 1
    assert listed.items[0].id == item.id

    with pytest.raises(HTTPException) as exc:
        await routes.list_matter_inbound_email(
            str(matter.id), request, "invalid", FakeDB()
        )
    assert exc.value.status_code == 400

    monkeypatch.setattr(routes, "_pending_inbound_or_404", AsyncMock(return_value=item))
    communication_id = uuid.uuid4()
    monkeypatch.setattr(
        routes,
        "file_inbound_email",
        AsyncMock(return_value=SimpleNamespace(id=communication_id)),
    )
    accepted = await routes.accept_matter_inbound_email(
        matter.id, item.id, request, FakeDB()
    )
    assert accepted.communication_log_id == communication_id

    monkeypatch.setattr(routes, "remove_quarantined_message", lambda _: None)
    rejected_db = FakeDB()
    rejected = await routes.reject_matter_inbound_email(
        matter.id, item.id, request, rejected_db
    )
    assert rejected.status == "rejected"
    assert item.raw_storage_path is None
    assert rejected_db.commits == 1
