"""Mediation portal sessions honor tenant suspension and production RLS."""

import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.main import app
from app.middleware.tenant import get_portal_context
from app.models.mediation import MediationInvite, MediationParty
from app.models.plugin import MediationCase
from app.models.tenant import Tenant
from app.services.portal_token import create_portal_token, create_user_token


def _request(token: str):
    return SimpleNamespace(
        cookies={},
        headers={"Authorization": f"Bearer {token}"},
        app=app,
    )


async def _firm_client_case(db_session, tenant, user):
    user.role = "client"
    case = MediationCase(
        tenant_id=tenant.id,
        title="Portal RLS case",
        case_name="Portal RLS case",
    )
    db_session.add(case)
    await db_session.flush()
    party = MediationParty(
        tenant_id=tenant.id,
        case_id=case.id,
        role="our_client",
        name=user.full_name,
        email=user.email,
        user_id=user.id,
    )
    db_session.add(party)
    await db_session.commit()
    return case, party


@pytest.mark.asyncio
async def test_magic_portal_rejects_inactive_tenant(db_session, test_tenant, test_user):
    case = MediationCase(tenant_id=test_tenant.id, title="Magic portal case")
    db_session.add(case)
    await db_session.flush()
    party = MediationParty(
        tenant_id=test_tenant.id,
        case_id=case.id,
        role="opposing_party",
        name="Other Party",
    )
    db_session.add(party)
    await db_session.flush()
    invite = MediationInvite(
        tenant_id=test_tenant.id,
        case_id=case.id,
        party_id=party.id,
        token_hash="portal-test-token-hash",
        kind="portal_magic",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    db_session.add(invite)
    await db_session.commit()
    token = create_portal_token(
        tenant_id=str(test_tenant.id),
        case_id=str(case.id),
        party_id=str(party.id),
        party_role=party.role,
        invite_id=str(invite.id),
    )

    test_tenant.is_active = False
    await db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await get_portal_context(_request(token), db_session)
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Tenant account is inactive"


@pytest.mark.asyncio
async def test_firm_client_portal_rejects_inactive_tenant(
    db_session, test_tenant, test_user
):
    case, _party = await _firm_client_case(db_session, test_tenant, test_user)
    token = create_user_token(
        user_id=str(test_user.id),
        tenant_id=str(test_tenant.id),
        role="client",
        email=test_user.email,
    )
    test_tenant.is_active = False
    await db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await get_portal_context(_request(token), db_session, case_id=str(case.id))
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Tenant account is inactive"


@pytest.mark.asyncio
async def test_firm_client_identity_is_bound_to_signed_tenant(
    db_session, test_tenant, test_user
):
    case, _party = await _firm_client_case(db_session, test_tenant, test_user)
    other_tenant = Tenant(
        id=uuid.uuid4(),
        name="Other Firm",
        domain="other-portal-firm.test",
        billing_tier="payg",
        is_active=True,
    )
    db_session.add(other_tenant)
    await db_session.commit()
    token = create_user_token(
        user_id=str(test_user.id),
        tenant_id=str(other_tenant.id),
        role="client",
        email=test_user.email,
    )

    with pytest.raises(HTTPException) as exc_info:
        await get_portal_context(_request(token), db_session, case_id=str(case.id))
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_firm_client_rejects_malformed_tenant_claim(db_session, test_user):
    token = create_user_token(
        user_id=str(test_user.id),
        tenant_id="not-a-uuid",
        role="client",
        email=test_user.email,
    )

    with pytest.raises(HTTPException) as exc_info:
        await get_portal_context(_request(token), db_session, case_id=str(uuid.uuid4()))
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_firm_client_lookup_succeeds_under_runtime_rls(
    db_session, test_tenant, test_user
):
    url = os.getenv("RLS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("RLS_TEST_DATABASE_URL is required for runtime-role integration")

    case, party = await _firm_client_case(db_session, test_tenant, test_user)
    token = create_user_token(
        user_id=str(test_user.id),
        tenant_id=str(test_tenant.id),
        role="client",
        email=test_user.email,
    )
    engine = create_async_engine(url, pool_pre_ping=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as runtime_db:
            context = await get_portal_context(
                _request(token), runtime_db, case_id=str(case.id)
            )
            assert context.tenant_id == str(test_tenant.id)
            assert context.party_id == str(party.id)
            assert context.user.id == test_user.id
            current_tenant = await runtime_db.scalar(
                text("SELECT current_setting('app.current_tenant_id', true)")
            )
            assert current_tenant == str(test_tenant.id)
    finally:
        await engine.dispose()
