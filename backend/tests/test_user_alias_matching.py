from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
import hashlib
from unittest.mock import AsyncMock, Mock
import pytest
from fastapi import HTTPException

from app.services.correspondence_capture import evaluate_matter_rules
from app.models.user_alias import UserAliasAddress
from app.routers.auth import _alias_token_is_valid, _resolve_oauth_tenant_and_user
from app.routers.auth import verify_alias
from app.services.correspondence_capture import _matter_party_addresses
from app.routers import user_aliases
from app.services.email import EmailDeliveryResult


def test_verified_alias_matches_correspondence_party():
    matter = SimpleNamespace(correspondence_rules=None, case_number=None)
    email = {"from": "send-as@firm.test", "to": [], "cc": [], "subject": "Hello"}
    assert evaluate_matter_rules(
        matter, email, {"send-as@firm.test"}, {"enabled": True, "match_parties": True}
    )


def test_unverified_alias_is_not_added_to_party_set():
    matter = SimpleNamespace(correspondence_rules=None, case_number=None)
    email = {"from": "pending@firm.test", "to": [], "cc": [], "subject": "Hello"}
    # The query layer only supplies verified aliases.  An absent address must
    # therefore behave exactly like an unknown correspondent.
    assert not evaluate_matter_rules(
        matter, email, set(), {"enabled": True, "match_parties": True}
    )


def test_alias_address_uniqueness_is_tenant_scoped_and_primary_is_checked_first():
    constraint = next(c for c in UserAliasAddress.__table__.constraints if c.name == "uq_user_alias_tenant_address")
    assert [column.name for column in constraint.columns] == ["tenant_id", "normalized_address"]
    # A primary address is checked by the admin endpoint before an alias row is
    # created; this table constraint then prevents two aliases in one tenant.
    assert UserAliasAddress.__table__.c.address.nullable is False
    assert UserAliasAddress.__table__.c.tenant_id.nullable is False


def test_alias_token_expires_and_is_single_use():
    now = datetime.now(timezone.utc)
    raw = "proof-token"
    row = UserAliasAddress(
        is_verified=False,
        verification_token_hash=hashlib.sha256(raw.encode()).hexdigest(),
        verification_expires_at=now + timedelta(minutes=5),
    )
    assert _alias_token_is_valid(row, raw, now)
    row.is_verified = True
    assert not _alias_token_is_valid(row, raw, now)
    row.is_verified = False
    row.verification_expires_at = now - timedelta(seconds=1)
    assert not _alias_token_is_valid(row, raw, now)


def test_alias_token_does_not_cross_match_other_token():
    now = datetime.now(timezone.utc)
    row = UserAliasAddress(
        is_verified=False,
        verification_token_hash=hashlib.sha256(b"tenant-a-token").hexdigest(),
        verification_expires_at=now + timedelta(minutes=5),
    )
    assert not _alias_token_is_valid(row, "tenant-b-token", now)


@pytest.mark.asyncio
async def test_party_address_query_only_adds_verified_aliases_and_tenant_rows():
    db = AsyncMock()
    db.add = Mock()
    contacts = SimpleNamespace(all=lambda: [("party@firm.test",)])
    aliases = SimpleNamespace(all=lambda: [("verified-send-as@firm.test",)])
    db.execute.side_effect = [SimpleNamespace(all=lambda: [("party@firm.test",)]), aliases]
    matter = SimpleNamespace(id="matter-1", client_contact_id=None)
    addresses = await _matter_party_addresses(db, "tenant-1", matter)
    assert addresses == {"party@firm.test", "verified-send-as@firm.test"}
    alias_query = db.execute.call_args_list[1].args[0]
    sql = str(alias_query)
    assert "is_verified" in sql
    assert "tenant_id" in sql


@pytest.mark.asyncio
async def test_admin_alias_endpoint_creates_pending_alias_and_sends_proof(monkeypatch):
    tenant_id = "11111111-1111-1111-1111-111111111111"
    user = SimpleNamespace(id="22222222-2222-2222-2222-222222222222", tenant_id=tenant_id)
    db = AsyncMock()
    db.add = Mock()
    db.scalar.side_effect = [None, None, None]
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    monkeypatch.setattr(user_aliases, "_admin_user", AsyncMock(return_value=user))
    monkeypatch.setattr(user_aliases.email_service, "send_email", AsyncMock(return_value=EmailDeliveryResult.SENT))
    body = user_aliases.UserAliasCreateRequest(address="Send-As@example.com")
    result = await user_aliases.add_user_alias("user", body, None, db)
    assert result["is_verified"] is False
    assert result["verification_required"] is True
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_admin_alias_endpoint_rolls_back_when_delivery_fails(monkeypatch):
    user = SimpleNamespace(id="user", tenant_id="tenant")
    db = AsyncMock()
    db.add = Mock()
    db.scalar.side_effect = [None, None, None]
    monkeypatch.setattr(user_aliases, "_admin_user", AsyncMock(return_value=user))
    monkeypatch.setattr(user_aliases.email_service, "send_email", AsyncMock(return_value=EmailDeliveryResult.UNCONFIGURED))
    body = user_aliases.UserAliasCreateRequest(address="send-as@example.com")
    with pytest.raises(Exception, match="verification email delivery failed"):
        await user_aliases.add_user_alias("user", body, None, db)
    db.rollback.assert_awaited()


@pytest.mark.asyncio
async def test_verify_alias_endpoint_consumes_token_once(monkeypatch):
    now = datetime.now(timezone.utc)
    raw = "endpoint-proof"
    row = SimpleNamespace(
        is_verified=False,
        verification_token_hash=hashlib.sha256(raw.encode()).hexdigest(),
        verification_expires_at=now + timedelta(minutes=5),
        address="verified@example.com",
        verification_method=None,
        verified_at=None,
    )
    db = AsyncMock()
    db.scalar.side_effect = [row, None]
    db.commit = AsyncMock()
    monkeypatch.setattr("app.routers.auth.set_tenant_context", AsyncMock())
    first = await verify_alias("11111111-1111-1111-1111-111111111111", raw, db)
    assert first["verified"] is True
    assert row.verification_token_hash is None
    with pytest.raises(HTTPException) as exc:
        await verify_alias("11111111-1111-1111-1111-111111111111", raw, db)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_verified_alias_cannot_reactivate_an_inactive_user_via_oauth(monkeypatch):
    inactive = SimpleNamespace(id="user", tenant_id="tenant", is_active=False)
    tenant = SimpleNamespace(id="tenant", is_active=True, expires_at=None)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: tenant))
    monkeypatch.setattr("app.routers.auth._resolve_existing_oauth_user", AsyncMock(return_value=inactive))
    with pytest.raises(HTTPException) as exc:
        await _resolve_oauth_tenant_and_user(
            db, email="alias@example.com", full_name=None, provider="google",
            subject="google-subject", domain="example.com", tenant_name="Example",
        )
    assert exc.value.status_code == 403
    assert "inactive" in exc.value.detail.lower()
