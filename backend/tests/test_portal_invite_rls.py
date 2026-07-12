"""Opaque portal invitation acceptance under the production runtime role."""

import asyncio
import hashlib
import os
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.middleware.rate_limit import AUTH_LIMITS
from app.models.client_portal import ClientPortalInvite
from app.models.mediation import MediationInvite, MediationParty
from app.models.plugin import Matter, MediationCase
from app.routers import client_portal, mediation_portal
from app.schemas.client_portal import ClientPortalAcceptRequest
from app.schemas.mediation import PortalAcceptRequest
from app.services.portal_invites import PORTAL_INVITE_UNAVAILABLE_DETAIL


async def _seed_invites(db_session, tenant, user):
    mediation_raw = "mediation-opaque-runtime-token"
    client_raw = "client-opaque-runtime-token"
    case = MediationCase(tenant_id=tenant.id, title="Opaque invite case")
    matter = Matter(
        tenant_id=tenant.id,
        user_id=user.id,
        slug="opaque-invite-matter",
        matter_name="Opaque Invite Matter",
        portal_enabled=True,
    )
    db_session.add_all([case, matter])
    await db_session.flush()
    party = MediationParty(
        tenant_id=tenant.id,
        case_id=case.id,
        role="opposing_party",
        name="Portal Party",
        email="portal-party@example.test",
    )
    db_session.add(party)
    await db_session.flush()
    mediation_invite = MediationInvite(
        tenant_id=tenant.id,
        case_id=case.id,
        party_id=party.id,
        token_hash=hashlib.sha256(mediation_raw.encode()).hexdigest(),
        kind="portal_magic",
        email=party.email,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    client_invite = ClientPortalInvite(
        tenant_id=tenant.id,
        matter_id=matter.id,
        token_hash=hashlib.sha256(client_raw.encode()).hexdigest(),
        email="matter-client@example.test",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        created_by_user_id=user.id,
    )
    db_session.add_all([mediation_invite, client_invite])
    await db_session.commit()
    return mediation_raw, client_raw, mediation_invite, client_invite


def test_public_invite_exchanges_use_existing_ip_limiter():
    assert AUTH_LIMITS["/api/portal/mediation/accept"] == (10, 600)
    assert AUTH_LIMITS["/api/portal/client/accept"] == (10, 600)


@pytest.mark.asyncio
async def test_both_opaque_invites_accept_under_runtime_rls(
    db_session, test_tenant, test_user
):
    url = os.getenv("RLS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("RLS_TEST_DATABASE_URL is required for runtime-role integration")
    mediation_raw, client_raw, mediation_invite, client_invite = await _seed_invites(
        db_session, test_tenant, test_user
    )

    engine = create_async_engine(url, pool_pre_ping=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as runtime_db:
            mediation_result = await mediation_portal.accept_invite(
                PortalAcceptRequest(token=mediation_raw), Response(), runtime_db
            )
            assert mediation_result.case_id == str(mediation_invite.case_id)

            client_result = await client_portal.accept_invite(
                ClientPortalAcceptRequest(token=client_raw), Response(), runtime_db
            )
            assert client_result.matter_id == str(client_invite.matter_id)

            bypass = await runtime_db.scalar(
                text("SELECT current_setting('app.rls_bypass', true)")
            )
            assert bypass in (None, "", "off")

        await db_session.refresh(mediation_invite)
        await db_session.refresh(client_invite)
        assert mediation_invite.accepted_at is not None
        assert client_invite.accepted_at is not None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_suspended_tenant_invites_match_generic_invalid_token_failure(
    db_session, test_tenant, test_user
):
    url = os.getenv("RLS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("RLS_TEST_DATABASE_URL is required for runtime-role integration")
    mediation_raw, client_raw, _mediation_invite, _client_invite = await _seed_invites(
        db_session, test_tenant, test_user
    )
    test_tenant.is_active = False
    await db_session.commit()

    engine = create_async_engine(url, pool_pre_ping=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as runtime_db:
            failures = []
            for action in (
                lambda: mediation_portal.accept_invite(
                    PortalAcceptRequest(token=mediation_raw), Response(), runtime_db
                ),
                lambda: client_portal.accept_invite(
                    ClientPortalAcceptRequest(token=client_raw), Response(), runtime_db
                ),
                lambda: mediation_portal.accept_invite(
                    PortalAcceptRequest(token="invalid-token"),
                    Response(),
                    runtime_db,
                ),
            ):
                with pytest.raises(HTTPException) as exc_info:
                    await action()
                failures.append((exc_info.value.status_code, exc_info.value.detail))

            assert failures == [(404, PORTAL_INVITE_UNAVAILABLE_DETAIL)] * 3
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_mediation_accept_is_single_use_under_runtime_rls(
    db_session, test_tenant, test_user
):
    url = os.getenv("RLS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("RLS_TEST_DATABASE_URL is required for runtime-role integration")
    mediation_raw, _client_raw, _mediation_invite, _client_invite = await _seed_invites(
        db_session, test_tenant, test_user
    )

    engine = create_async_engine(url, pool_pre_ping=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def attempt_accept():
        async with maker() as runtime_db:
            try:
                result = await mediation_portal.accept_invite(
                    PortalAcceptRequest(token=mediation_raw), Response(), runtime_db
                )
                return 200, result.case_id
            except HTTPException as exc:
                return exc.status_code, exc.detail

    try:
        outcomes = await asyncio.gather(attempt_accept(), attempt_accept())
        assert sorted(status for status, _detail in outcomes) == [200, 404]
        failure = next(detail for status, detail in outcomes if status == 404)
        assert failure == PORTAL_INVITE_UNAVAILABLE_DETAIL
    finally:
        await engine.dispose()
