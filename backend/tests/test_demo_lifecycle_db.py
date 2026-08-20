import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database import set_tenant_context
from app.models.contact import Contact
from app.models.demo_session import DemoSession
from app.models.document import Chunk, Document
from app.models.plugin import Matter
from app.models.tenant import Tenant, TenantSettings
from app.models.user import User
from app.services.demo_clone import clone_demo_fixture
from app.services.demo_purge import DemoPurgeRefused, purge_demo_tenant
from app.services.demo_quota import (
    DemoQuotaExceeded,
    DemoReservation,
    release_demo_operation,
    reserve_demo_operation,
    settle_demo_operation,
)
from app.services import demo_clone, demo_purge


def _tenant(*, tenant_id, domain, billing_tier="fixture", expires_at=None):
    return Tenant(
        id=tenant_id,
        name=f"Synthetic {domain}",
        domain=domain,
        billing_tier=billing_tier,
        is_active=True,
        expires_at=expires_at,
    )


def _user(*, tenant_id, user_id, email):
    return User(
        id=user_id,
        tenant_id=tenant_id,
        email=email,
        full_name="Synthetic User",
        role="admin",
        oauth_provider="fixture",
        oauth_subject=str(user_id),
        premium_ai_enabled=False,
    )


@pytest.mark.asyncio
async def test_clone_remaps_relationships_json_and_files(
    db_session, tmp_path, monkeypatch
):
    fixture_id, target_id = uuid.uuid4(), uuid.uuid4()
    fixture_user_id, target_user_id = uuid.uuid4(), uuid.uuid4()
    contact_id, matter_id, document_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    monkeypatch.setattr(demo_clone.get_settings(), "UPLOAD_DIR", str(tmp_path))

    db_session.add_all(
        [
            _tenant(tenant_id=fixture_id, domain="fixture-v1.invalid"),
            _tenant(
                tenant_id=target_id,
                domain="demo-target.demo.invalid",
                billing_tier="demo",
            ),
        ]
    )
    await db_session.flush()
    await set_tenant_context(db_session, str(fixture_id))
    db_session.add_all(
        [
            _user(
                tenant_id=fixture_id,
                user_id=fixture_user_id,
                email="fixture-user@example.invalid",
            ),
            _user(
                tenant_id=target_id,
                user_id=target_user_id,
                email="target-user@example.invalid",
            ),
        ]
    )
    await db_session.flush()
    source_path = tmp_path / str(fixture_id) / str(document_id) / "agreement.txt"
    source_path.parent.mkdir(parents=True)
    source_path.write_text("Synthetic thirty-day notice clause", encoding="utf-8")
    # These models intentionally do not expose ORM relationships. Flush each
    # dependency level so the fixture follows the same FK order as the clone.
    db_session.add(
        Contact(
            id=contact_id,
            tenant_id=fixture_id,
            first_name="Avery",
            last_name="Synthetic",
            created_by_user_id=fixture_user_id,
        )
    )
    await db_session.flush()
    db_session.add(
        Matter(
            id=matter_id,
            tenant_id=fixture_id,
            user_id=fixture_user_id,
            slug="synthetic-matter",
            matter_name="Synthetic Matter",
            matter_type="litigation",
            client_contact_id=contact_id,
            key_dates={"source_document_id": str(document_id)},
        )
    )
    await db_session.flush()
    db_session.add(
        Document(
            id=document_id,
            tenant_id=fixture_id,
            user_id=fixture_user_id,
            matter_id=matter_id,
            filename=source_path.name,
            storage_path=str(source_path),
            status="indexed",
            chunk_count=1,
        )
    )
    await db_session.flush()
    db_session.add(
        Chunk(
            tenant_id=fixture_id,
            document_id=document_id,
            content="Synthetic thirty-day notice clause",
            chunk_index=0,
        )
    )
    await db_session.commit()

    counts = await clone_demo_fixture(
        db_session,
        fixture_tenant_id=fixture_id,
        target_tenant_id=target_id,
        target_user_id=target_user_id,
    )
    await db_session.commit()

    await set_tenant_context(db_session, str(target_id))
    cloned_matter = await db_session.scalar(
        select(Matter).where(Matter.tenant_id == target_id)
    )
    cloned_document = await db_session.scalar(
        select(Document).where(Document.tenant_id == target_id)
    )
    cloned_chunk = await db_session.scalar(
        select(Chunk).where(Chunk.tenant_id == target_id)
    )
    assert counts["documents"] == 1
    assert cloned_matter.id != matter_id
    assert cloned_matter.user_id == target_user_id
    assert cloned_matter.key_dates["source_document_id"] != str(document_id)
    assert cloned_document.id != document_id
    assert cloned_chunk.document_id == cloned_document.id
    assert Path(cloned_document.storage_path).is_relative_to(tmp_path / str(target_id))
    assert Path(cloned_document.storage_path).read_text(
        encoding="utf-8"
    ) == source_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_concurrent_final_quota_slot_has_one_winner(test_engine):
    fixture_id, tenant_id = uuid.uuid4(), uuid.uuid4()
    session_id = uuid.uuid4()
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as db:
        db.add_all(
            [
                _tenant(tenant_id=fixture_id, domain="quota-fixture.invalid"),
                _tenant(
                    tenant_id=tenant_id,
                    domain="quota.demo.invalid",
                    billing_tier="demo",
                    expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                ),
            ]
        )
        await db.flush()
        await set_tenant_context(db, str(tenant_id))
        db.add(
            DemoSession(
                id=session_id,
                tenant_id=tenant_id,
                fixture_tenant_id=fixture_id,
                fixture_version="quota-test",
                prospect_name="Synthetic Prospect",
                prospect_email="prospect@example.invalid",
                status="active",
                quota=1,
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            )
        )
        await db.commit()

    async def reserve(key):
        async with factory() as db:
            return await reserve_demo_operation(
                db,
                tenant_id=tenant_id,
                idempotency_key=key,
                surface="chat",
            )

    outcomes = await asyncio.gather(
        reserve("operation-20"), reserve("operation-21"), return_exceptions=True
    )
    winners = [result for result in outcomes if isinstance(result, DemoReservation)]
    losers = [result for result in outcomes if isinstance(result, DemoQuotaExceeded)]
    assert len(winners) == 1
    assert len(losers) == 1

    async with factory() as db:
        await settle_demo_operation(db, winners[0])
        await settle_demo_operation(db, winners[0])
        await set_tenant_context(db, str(tenant_id))
        demo = await db.get(DemoSession, session_id)
        assert (demo.used, demo.reserved) == (1, 0)


@pytest.mark.asyncio
async def test_released_operation_restores_capacity(test_engine):
    fixture_id, tenant_id = uuid.uuid4(), uuid.uuid4()
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as db:
        db.add_all(
            [
                _tenant(tenant_id=fixture_id, domain="release-fixture.invalid"),
                _tenant(
                    tenant_id=tenant_id,
                    domain="release.demo.invalid",
                    billing_tier="demo",
                    expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                ),
            ]
        )
        await db.flush()
        await set_tenant_context(db, str(tenant_id))
        db.add(
            DemoSession(
                tenant_id=tenant_id,
                fixture_tenant_id=fixture_id,
                fixture_version="release-test",
                prospect_name="Synthetic Prospect",
                prospect_email="prospect@example.invalid",
                status="active",
                quota=1,
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            )
        )
        await db.commit()
        reservation = await reserve_demo_operation(
            db,
            tenant_id=tenant_id,
            idempotency_key="failed-operation",
            surface="plugin",
        )
        await release_demo_operation(db, reservation)
        replacement = await reserve_demo_operation(
            db,
            tenant_id=tenant_id,
            idempotency_key="retry-operation",
            surface="plugin",
        )
        assert replacement is not None


@pytest.mark.asyncio
async def test_verified_purge_deletes_demo_and_preserves_fixture(
    db_session, tmp_path, monkeypatch
):
    fixture_id, tenant_id, user_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    monkeypatch.setattr(demo_purge.get_settings(), "UPLOAD_DIR", str(tmp_path))
    db_session.add_all(
        [
            _tenant(tenant_id=fixture_id, domain="purge-fixture.invalid"),
            _tenant(
                tenant_id=tenant_id,
                domain="purge.demo.invalid",
                billing_tier="demo",
                expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            ),
        ]
    )
    await db_session.flush()
    await set_tenant_context(db_session, str(tenant_id))
    db_session.add_all(
        [
            _user(
                tenant_id=tenant_id,
                user_id=user_id,
                email="purge-user@example.invalid",
            ),
            TenantSettings(tenant_id=tenant_id, custom_config={"plan": "demo"}),
            DemoSession(
                tenant_id=tenant_id,
                fixture_tenant_id=fixture_id,
                fixture_version="purge-test",
                prospect_name="Synthetic Prospect",
                prospect_email="prospect@example.invalid",
                status="active",
                quota=20,
                expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            ),
        ]
    )
    await db_session.commit()
    target_file = tmp_path / str(tenant_id) / "document.txt"
    target_file.parent.mkdir(parents=True)
    target_file.write_text("disposable", encoding="utf-8")

    await purge_demo_tenant(db_session, tenant_id)

    assert await db_session.scalar(select(Tenant.id).where(Tenant.id == fixture_id))
    assert (
        await db_session.scalar(select(Tenant.id).where(Tenant.id == tenant_id)) is None
    )
    assert not target_file.exists()


@pytest.mark.asyncio
async def test_purge_refuses_session_already_claimed_by_another_worker(
    db_session, tmp_path, monkeypatch
):
    fixture_id, tenant_id = uuid.uuid4(), uuid.uuid4()
    monkeypatch.setattr(demo_purge.get_settings(), "UPLOAD_DIR", str(tmp_path))
    db_session.add_all(
        [
            _tenant(tenant_id=fixture_id, domain="purge-lock-fixture.invalid"),
            _tenant(
                tenant_id=tenant_id,
                domain="purge-lock.demo.invalid",
                billing_tier="demo",
                expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            ),
        ]
    )
    # DemoSession has two tenant FKs without ORM relationships; flush the
    # referenced tenants before inserting the claimed session row.
    await db_session.flush()
    await set_tenant_context(db_session, str(tenant_id))
    db_session.add(
        DemoSession(
            tenant_id=tenant_id,
            fixture_tenant_id=fixture_id,
            fixture_version="purge-lock-test",
            prospect_name="Synthetic Prospect",
            prospect_email="prospect@example.invalid",
            status="purging",
            quota=20,
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
    )
    await db_session.commit()

    with pytest.raises(DemoPurgeRefused, match="already being purged"):
        await purge_demo_tenant(db_session, tenant_id)

    assert await db_session.scalar(select(Tenant.id).where(Tenant.id == tenant_id))
