import asyncio
import uuid
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select, text
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


@pytest.mark.asyncio
async def test_purge_reclaims_session_stranded_by_a_crashed_worker(
    db_session, tmp_path, monkeypatch
):
    """A worker that dies after claiming the row must not strand demo data.

    The claim guard keeps two live workers apart, but a process that is killed
    between committing ``purging`` and reaching a terminal state leaves the row
    claimed with nobody working it. The hourly job has to pick that tenant back
    up once the reclaim window has passed, or the synthetic workspace outlives
    its expiry forever.
    """
    fixture_id, tenant_id = uuid.uuid4(), uuid.uuid4()
    monkeypatch.setattr(demo_purge.get_settings(), "UPLOAD_DIR", str(tmp_path))
    stranded_at = datetime.now(timezone.utc) - (
        demo_purge._PURGE_RECLAIM_AFTER + timedelta(minutes=5)
    )
    db_session.add_all(
        [
            _tenant(tenant_id=fixture_id, domain="purge-reclaim-fixture.invalid"),
            _tenant(
                tenant_id=tenant_id,
                domain="purge-reclaim.demo.invalid",
                billing_tier="demo",
                expires_at=stranded_at,
            ),
        ]
    )
    await db_session.flush()
    await set_tenant_context(db_session, str(tenant_id))
    db_session.add(
        DemoSession(
            tenant_id=tenant_id,
            fixture_tenant_id=fixture_id,
            fixture_version="purge-reclaim-test",
            prospect_name="Synthetic Prospect",
            prospect_email="prospect@example.invalid",
            status="purging",
            quota=20,
            expires_at=stranded_at,
            purge_started_at=stranded_at,
        )
    )
    await db_session.commit()

    await purge_demo_tenant(db_session, tenant_id)

    assert (
        await db_session.scalar(select(Tenant.id).where(Tenant.id == tenant_id)) is None
    )


@pytest.mark.asyncio
async def test_purge_does_not_reclaim_a_fresh_claim_on_a_long_expired_tenant(
    db_session, tmp_path, monkeypatch
):
    """Staleness is measured from the claim, never from tenant expiry.

    A tenant that expired well before its first purge attempt — a missed
    scheduler run, a deploy, a restart — is already past any expiry-based
    window at the moment a live worker claims it. Reclaiming on that basis
    would put a second worker into file and row deletion alongside the first.
    """
    fixture_id, tenant_id = uuid.uuid4(), uuid.uuid4()
    monkeypatch.setattr(demo_purge.get_settings(), "UPLOAD_DIR", str(tmp_path))
    now = datetime.now(timezone.utc)
    long_expired = now - (demo_purge._PURGE_RECLAIM_AFTER * 5)
    db_session.add_all(
        [
            _tenant(tenant_id=fixture_id, domain="purge-fresh-fixture.invalid"),
            _tenant(
                tenant_id=tenant_id,
                domain="purge-fresh-claim.demo.invalid",
                billing_tier="demo",
                expires_at=long_expired,
            ),
        ]
    )
    await db_session.flush()
    await set_tenant_context(db_session, str(tenant_id))
    db_session.add(
        DemoSession(
            tenant_id=tenant_id,
            fixture_tenant_id=fixture_id,
            fixture_version="purge-fresh-claim-test",
            prospect_name="Synthetic Prospect",
            prospect_email="prospect@example.invalid",
            status="purging",
            quota=20,
            expires_at=long_expired,
            # Another worker claimed this a moment ago and is still working it.
            purge_started_at=now - timedelta(seconds=5),
        )
    )
    await db_session.commit()

    with pytest.raises(DemoPurgeRefused, match="already being purged"):
        await purge_demo_tenant(db_session, tenant_id)

    assert await db_session.scalar(select(Tenant.id).where(Tenant.id == tenant_id))


@pytest.mark.asyncio
async def test_stale_purge_claim_is_fenced_by_an_existing_advisory_lock(
    db_session, test_engine, tmp_path, monkeypatch
):
    """A stale row cannot bypass a live per-tenant purge lock.

    ``purge_started_at`` is intentionally old enough to make the row
    reclaimable.  The independent transaction-scoped advisory lock represents
    the original worker still being alive.  The second worker must refuse
    before touching either the filesystem or purge tables, leaving the claim
    and tenant intact.
    """
    fixture_id, tenant_id = uuid.uuid4(), uuid.uuid4()
    monkeypatch.setattr(demo_purge.get_settings(), "UPLOAD_DIR", str(tmp_path))
    stale_at = datetime.now(timezone.utc) - (
        demo_purge._PURGE_RECLAIM_AFTER + timedelta(minutes=5)
    )
    db_session.add_all(
        [
            _tenant(tenant_id=fixture_id, domain="purge-advisory-fixture.invalid"),
            _tenant(
                tenant_id=tenant_id,
                domain="purge-advisory.demo.invalid",
                billing_tier="demo",
                expires_at=stale_at,
            ),
        ]
    )
    await db_session.flush()
    await set_tenant_context(db_session, str(tenant_id))
    db_session.add(
        DemoSession(
            tenant_id=tenant_id,
            fixture_tenant_id=fixture_id,
            fixture_version="purge-advisory-lock-test",
            prospect_name="Synthetic Prospect",
            prospect_email="prospect@example.invalid",
            status="purging",
            quota=20,
            expires_at=stale_at,
            purge_started_at=stale_at,
        )
    )
    await db_session.commit()

    def _filesystem_delete_must_not_start(_tenant_id):
        pytest.fail("filesystem deletion began while the advisory lock was held")

    def _table_plan_must_not_start():
        pytest.fail("database purge planning began while the advisory lock was held")

    monkeypatch.setattr(
        demo_purge, "_remove_tenant_files", _filesystem_delete_must_not_start
    )
    monkeypatch.setattr(demo_purge, "_purge_tables", _table_plan_must_not_start)

    async with test_engine.connect() as lock_connection:
        async with lock_connection.begin():
            await lock_connection.execute(
                text(
                    "SELECT pg_advisory_xact_lock(" "hashtextextended(:lock_name, 0))"
                ),
                {"lock_name": demo_purge._tenant_purge_lock_name(tenant_id)},
            )

            with pytest.raises(DemoPurgeRefused, match="already being purged"):
                await purge_demo_tenant(db_session, tenant_id)

    await set_tenant_context(db_session, str(tenant_id))
    tenant = await db_session.scalar(select(Tenant).where(Tenant.id == tenant_id))
    demo = await db_session.scalar(
        select(DemoSession).where(DemoSession.tenant_id == tenant_id)
    )
    assert tenant is not None
    assert demo is not None
    assert demo.status == "purging"


@pytest.mark.asyncio
async def test_purge_stamps_the_claim_so_the_next_worker_measures_its_own_window(
    db_session, tmp_path, monkeypatch
):
    """The claim commit must record when it happened, including on a reclaim."""
    fixture_id, tenant_id = uuid.uuid4(), uuid.uuid4()
    monkeypatch.setattr(demo_purge.get_settings(), "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(
        demo_purge,
        "_purge_tables",
        lambda: (_ for _ in ()).throw(DemoPurgeRefused("stop after the claim")),
    )
    expired_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db_session.add_all(
        [
            _tenant(tenant_id=fixture_id, domain="purge-stamp-fixture.invalid"),
            _tenant(
                tenant_id=tenant_id,
                domain="purge-stamp.demo.invalid",
                billing_tier="demo",
                expires_at=expired_at,
            ),
        ]
    )
    await db_session.flush()
    await set_tenant_context(db_session, str(tenant_id))
    db_session.add(
        DemoSession(
            tenant_id=tenant_id,
            fixture_tenant_id=fixture_id,
            fixture_version="purge-stamp-test",
            prospect_name="Synthetic Prospect",
            prospect_email="prospect@example.invalid",
            status="active",
            quota=20,
            expires_at=expired_at,
        )
    )
    await db_session.commit()

    with pytest.raises(DemoPurgeRefused, match="stop after the claim"):
        await purge_demo_tenant(db_session, tenant_id)

    await set_tenant_context(db_session, str(tenant_id))
    claimed_at = await db_session.scalar(
        select(DemoSession.purge_started_at).where(DemoSession.tenant_id == tenant_id)
    )
    assert claimed_at is not None


@pytest.mark.asyncio
async def test_purge_refuses_a_claim_with_no_recorded_start(
    db_session, tmp_path, monkeypatch
):
    """An unstamped claim fails safe: refuse rather than risk a second worker."""
    fixture_id, tenant_id = uuid.uuid4(), uuid.uuid4()
    monkeypatch.setattr(demo_purge.get_settings(), "UPLOAD_DIR", str(tmp_path))
    expired_at = datetime.now(timezone.utc) - (demo_purge._PURGE_RECLAIM_AFTER * 5)
    db_session.add_all(
        [
            _tenant(tenant_id=fixture_id, domain="purge-unstamped-fixture.invalid"),
            _tenant(
                tenant_id=tenant_id,
                domain="purge-unstamped.demo.invalid",
                billing_tier="demo",
                expires_at=expired_at,
            ),
        ]
    )
    await db_session.flush()
    await set_tenant_context(db_session, str(tenant_id))
    db_session.add(
        DemoSession(
            tenant_id=tenant_id,
            fixture_tenant_id=fixture_id,
            fixture_version="purge-unstamped-test",
            prospect_name="Synthetic Prospect",
            prospect_email="prospect@example.invalid",
            status="purging",
            quota=20,
            expires_at=expired_at,
            purge_started_at=None,
        )
    )
    await db_session.commit()

    with pytest.raises(DemoPurgeRefused, match="already being purged"):
        await purge_demo_tenant(db_session, tenant_id)


@pytest.mark.asyncio
async def test_purge_records_failure_when_file_removal_is_refused(
    db_session, tmp_path, monkeypatch
):
    """File-removal failures must reach the terminal ``failed`` state.

    Otherwise the session stays claimed with no audit trail and the next run
    refuses it as already being purged.
    """
    fixture_id, tenant_id = uuid.uuid4(), uuid.uuid4()
    monkeypatch.setattr(demo_purge.get_settings(), "UPLOAD_DIR", str(tmp_path))

    def _explode(_tenant_id):
        raise DemoPurgeRefused("Demo storage path failed its containment guard")

    monkeypatch.setattr(demo_purge, "_remove_tenant_files", _explode)
    db_session.add_all(
        [
            _tenant(tenant_id=fixture_id, domain="purge-files-fixture.invalid"),
            _tenant(
                tenant_id=tenant_id,
                domain="purge-files.demo.invalid",
                billing_tier="demo",
                expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            ),
        ]
    )
    await db_session.flush()
    await set_tenant_context(db_session, str(tenant_id))
    db_session.add(
        DemoSession(
            tenant_id=tenant_id,
            fixture_tenant_id=fixture_id,
            fixture_version="purge-files-test",
            prospect_name="Synthetic Prospect",
            prospect_email="prospect@example.invalid",
            status="active",
            quota=20,
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
    )
    await db_session.commit()

    with pytest.raises(DemoPurgeRefused, match="containment guard"):
        await purge_demo_tenant(db_session, tenant_id)

    await set_tenant_context(db_session, str(tenant_id))
    status = await db_session.scalar(
        select(DemoSession.status).where(DemoSession.tenant_id == tenant_id)
    )
    assert status == "failed"


def test_claim_timestamps_are_normalised_to_utc():
    """A naive claim must not crash the staleness comparison."""
    aware = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
    naive = datetime(2026, 8, 21, 12, 0)

    assert demo_purge._claim_started_at(SimpleNamespace(purge_started_at=None)) is None
    assert (
        demo_purge._claim_started_at(SimpleNamespace(purge_started_at=aware)) == aware
    )
    normalised = demo_purge._claim_started_at(SimpleNamespace(purge_started_at=naive))
    assert normalised == aware
    assert normalised.tzinfo is timezone.utc


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("billing_tier", "domain", "expires_in"),
    [
        # A paying tenant must never be purgeable, whatever else lines up.
        ("payg", "real-customer.demo.invalid", timedelta(minutes=-1)),
        # Nor a demo-tier tenant outside the disposable demo domain.
        ("demo", "real-customer.example.com", timedelta(minutes=-1)),
        # Nor one that has not expired yet.
        ("demo", "not-yet.demo.invalid", timedelta(hours=1)),
    ],
)
async def test_purge_refuses_any_tenant_that_is_not_an_expired_disposable_demo(
    db_session, tmp_path, monkeypatch, billing_tier, domain, expires_in
):
    tenant_id = uuid.uuid4()
    monkeypatch.setattr(demo_purge.get_settings(), "UPLOAD_DIR", str(tmp_path))
    db_session.add(
        _tenant(
            tenant_id=tenant_id,
            domain=domain,
            billing_tier=billing_tier,
            expires_at=datetime.now(timezone.utc) + expires_in,
        )
    )
    await db_session.commit()

    with pytest.raises(DemoPurgeRefused, match="not an expired disposable demo"):
        await purge_demo_tenant(db_session, tenant_id)

    assert await db_session.scalar(select(Tenant.id).where(Tenant.id == tenant_id))
