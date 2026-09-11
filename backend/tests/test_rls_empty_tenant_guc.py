"""Tenant RLS fails closed, not with an exception, when the tenant setting is empty.

A commit inside a unit of work clears the transaction-local tenant setting, and
within the same session it then reads as ''. Before migration 173, 39 policies
cast that straight to ``uuid`` and raised ``invalid input syntax for type uuid:
""``; production hit it on 2026-09-11 when an OAuth refresh committed in the
middle of a cloud sync. Those policies now use ``NULLIF(..., '')::uuid``, so the
same state matches no rows and rejects writes.

Runs under the least-privilege runtime role against the migrated schema.
"""

import os
import uuid

import pytest
from sqlalchemy import insert, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from app.models.cloud_metadata import CloudMetadata
from app.models.tenant import Tenant

RLS_URL = os.getenv("RLS_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not RLS_URL,
    reason="RLS_TEST_DATABASE_URL is required for runtime-role integration",
)

UNSAFE_TENANT_CAST = (
    r"current_setting\('app\.(current_)?tenant_id'::text(, true)?\)\)::uuid"
)
SET_TENANT = text(
    "SELECT set_config('app.current_tenant_id', :tenant_id, true), "
    "set_config('app.tenant_id', :tenant_id, true)"
)
METADATA = CloudMetadata.__table__


@pytest.mark.asyncio
async def test_no_tenant_policy_casts_the_setting_without_nullif(db_session):
    policy_count = await db_session.scalar(
        text(
            "SELECT count(*) FROM pg_policies WHERE tablename = 'cloud_metadata_index'"
        )
    )
    # Guard against passing vacuously on a schema built without migrations.
    assert policy_count >= 1

    unsafe = (
        await db_session.execute(
            text(
                "SELECT tablename, policyname FROM pg_policies "
                "WHERE (coalesce(qual, '') || ' ' || coalesce(with_check, '')) "
                "~ :unsafe "
                "AND (coalesce(qual, '') || ' ' || coalesce(with_check, '')) "
                "!~ 'NULLIF' "
                "ORDER BY 1, 2"
            ),
            {"unsafe": UNSAFE_TENANT_CAST},
        )
    ).all()
    assert unsafe == []


@pytest.mark.asyncio
async def test_lost_tenant_context_matches_no_rows_and_rejects_writes(
    db_session, test_tenant
):
    other_tenant = Tenant(
        id=uuid.uuid4(),
        name="Other Firm",
        domain="otherfirm.example",
        billing_tier="payg",
        is_active=True,
    )
    db_session.add(other_tenant)
    db_session.add(
        CloudMetadata(
            tenant_id=test_tenant.id,
            provider="microsoft",
            object_type="file",
            object_id="tenant-a-file",
        )
    )
    await db_session.commit()
    tenant_uuid = test_tenant.id
    other_tenant_id = str(other_tenant.id)

    engine = create_async_engine(RLS_URL)
    try:
        # One connection for the whole block, so the cleared setting is exactly
        # the session state a committing unit of work leaves behind.
        async with engine.connect() as connection:
            await connection.execute(SET_TENANT, {"tenant_id": str(tenant_uuid)})
            visible = (
                (await connection.execute(select(METADATA.c.object_id))).scalars().all()
            )
            assert visible == ["tenant-a-file"]
            await connection.commit()

            setting = await connection.scalar(
                text("SELECT current_setting('app.current_tenant_id', true)")
            )
            assert setting == ""
            # Before migration 173 this raised: invalid input syntax for type uuid.
            after_commit = (
                (await connection.execute(select(METADATA.c.object_id))).scalars().all()
            )
            assert after_commit == []

            with pytest.raises(DBAPIError) as refused:
                await connection.execute(
                    insert(METADATA).values(
                        tenant_id=tenant_uuid,
                        provider="microsoft",
                        object_type="file",
                        object_id="written-without-tenant-context",
                    )
                )
            assert "row-level security" in str(refused.value)
            await connection.rollback()

        # Isolation still holds once a real tenant is set.
        async with engine.connect() as connection:
            await connection.execute(SET_TENANT, {"tenant_id": other_tenant_id})
            seen_by_other = (
                (await connection.execute(select(METADATA.c.object_id))).scalars().all()
            )
            assert seen_by_other == []
            await connection.rollback()
    finally:
        await engine.dispose()
