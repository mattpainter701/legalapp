"""Focused behavioral contracts for one-time on-prem file-open intents."""

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config import Settings
from app.models.file_open_intent import FileOpenIntent
from app.schemas.file_open_intent import (
    FileOpenIntentCreate,
    FileOpenIntentOutcomeRequest,
    FileOpenIntentRedeemed,
)
from app.services import file_open_intents as service
from app.services.demo_registry import DEMO_TABLE_REGISTRY


class _Result:
    def __init__(self, value):
        self.value = value

    def first(self):
        return self.value

    def scalar_one_or_none(self):
        return self.value


class _DB:
    def __init__(self, *results):
        self.results = list(results)
        self.added = []
        self.commits = 0

    async def execute(self, _statement):
        return _Result(self.results.pop(0))

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.commits += 1

    async def refresh(self, value):
        if value.id is None:
            value.id = uuid.uuid4()


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: SimpleNamespace(FILE_OPEN_ENABLED=True),
    )
    monkeypatch.setattr(service, "set_tenant_context", AsyncMock())


def _ids():
    return [uuid.uuid4() for _ in range(7)]


def test_file_open_is_default_off_and_contract_has_no_path():
    assert Settings.model_fields["FILE_OPEN_ENABLED"].default is False
    request = FileOpenIntentCreate(file_id=str(uuid.uuid4()), matter_id=None)
    assert request.matter_id is None
    assert "path" not in request.model_dump()
    assert "unc_path" not in request.model_dump()
    assert {
        "source_id",
        "file_revision",
        "agent_id",
        "share_id",
        "nonce",
    }.issubset(FileOpenIntentRedeemed.model_fields)
    assert "path" not in FileOpenIntentRedeemed.model_fields
    assert DEMO_TABLE_REGISTRY["file_open_intents"].purge is True
    assert DEMO_TABLE_REGISTRY["file_open_intents"].clone is False


@pytest.mark.asyncio
async def test_create_is_source_based_matter_optional_and_handle_is_only_hashed():
    tenant_id, user_id, file_id, source_id, share_id, agent_id, _ = _ids()
    file_entry = SimpleNamespace(
        id=file_id,
        source_id=source_id,
        file_revision="r1",
        path=r"\\server\share\secret.pdf",
    )
    share = SimpleNamespace(id=share_id, share_path=r"\\server\share")
    agent = SimpleNamespace(id=agent_id)
    db = _DB((file_entry, share, agent))

    intent, handle = await service.create_intent(
        db,
        tenant_id=str(tenant_id),
        user_id=str(user_id),
        file_id=str(file_id),
        matter_id=None,
        action="open",
    )

    assert intent.source_id == source_id
    assert intent.revision == "r1"
    assert intent.matter_id is None
    assert intent.handle_hash == hashlib.sha256(handle.encode()).hexdigest()
    assert handle not in repr(intent.__dict__)
    assert not hasattr(intent, "path")
    assert db.commits == 1


def _intent(**overrides):
    now = datetime.now(timezone.utc)
    values = {
        "id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "file_id": uuid.uuid4(),
        "source_id": uuid.uuid4(),
        "agent_id": uuid.uuid4(),
        "share_id": uuid.uuid4(),
        "matter_id": None,
        "revision": "r1",
        "action": "open",
        "expires_at": now + timedelta(seconds=30),
        "redeemed_at": None,
        "redeemed_session_id": None,
        "redeemed_user_sid_hash": None,
        "outcome": None,
        "last_failure": None,
        "last_failure_at": None,
        "failure_count": 0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_redeem_is_single_use_and_records_authenticated_peer_identity():
    sid = "S-1-5-21-100"
    user_sid_hash = hashlib.sha256(sid.encode()).hexdigest()
    intent = _intent()
    db = _DB(intent, intent.file_id)

    redeemed = await service.redeem_intent(
        db,
        tenant_id=str(intent.tenant_id),
        agent_id=str(intent.agent_id),
        handle="a" * 32,
        action="open",
        session_id="7",
        user_sid=sid,
    )
    assert redeemed.redeemed_at is not None
    assert redeemed.redeemed_session_id == "7"
    assert redeemed.redeemed_user_sid_hash == user_sid_hash
    assert db.commits == 1

    with pytest.raises(service.OpenIntentError, match="already been redeemed"):
        await service.redeem_intent(
            _DB(intent),
            tenant_id=str(intent.tenant_id),
            agent_id=str(intent.agent_id),
            handle="a" * 32,
            action="open",
            session_id="7",
            user_sid=sid,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "intent,action,message",
    [
        (
            _intent(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)),
            "open",
            "expired",
        ),
        (_intent(action="show"), "open", "action"),
    ],
)
async def test_redeem_rejects_expired_or_mismatched_context(intent, action, message):
    with pytest.raises(service.OpenIntentError, match=message):
        await service.redeem_intent(
            _DB(intent),
            tenant_id=str(intent.tenant_id),
            agent_id=str(intent.agent_id),
            handle="b" * 32,
            action=action,
            session_id="1",
            user_sid="S-1-5-1",
        )


@pytest.mark.asyncio
async def test_redeem_fails_closed_for_wrong_tenant_agent_or_moved_revision():
    tenant_id, agent_id = uuid.uuid4(), uuid.uuid4()
    with pytest.raises(service.OpenIntentError, match="invalid or expired"):
        await service.redeem_intent(
            _DB(None),
            tenant_id=str(tenant_id),
            agent_id=str(agent_id),
            handle="c" * 32,
            action="open",
            session_id="1",
            user_sid="S-1-5-1",
        )
    intent = _intent(tenant_id=tenant_id, agent_id=agent_id)
    with pytest.raises(service.OpenIntentError, match="no longer available"):
        await service.redeem_intent(
            _DB(intent, None),
            tenant_id=str(tenant_id),
            agent_id=str(agent_id),
            handle="d" * 32,
            action="open",
            session_id="1",
            user_sid="S-1-5-1",
        )
    assert intent.redeemed_at is None


@pytest.mark.asyncio
async def test_outcome_audit_uses_redeemed_intent_id_and_stores_no_path_or_token():
    intent = _intent(redeemed_at=datetime.now(timezone.utc))
    db = _DB(intent)
    await service.record_outcome(
        db,
        tenant_id=str(intent.tenant_id),
        agent_id=str(intent.agent_id),
        intent_id=str(intent.id),
        outcome="access_denied",
    )
    assert intent.outcome == "access_denied"
    fields = set(FileOpenIntent.__table__.columns.keys())
    assert "path" not in fields and "file_path" not in fields
    assert "handle" not in fields
    assert FileOpenIntentOutcomeRequest(outcome="opened").outcome == "opened"
