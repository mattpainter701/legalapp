import asyncio
import hashlib
import uuid
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import Response
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker
from unittest.mock import AsyncMock, Mock

from app.database import set_tenant_context
from app.models.contact import Contact
from app.models.configurable_workflow import (
    ContactCustomFieldValue,
    CustomFieldDefinition,
    MatterCustomFieldValue,
    MatterWorkflowChecklistDefinition,
    MatterWorkflowFieldRequirement,
    MatterWorkflowRun,
    MatterWorkflowRunEvent,
    MatterWorkflowRunStep,
    MatterWorkflowStageDefinition,
    MatterWorkflowTemplate,
    MatterWorkflowTemplateVersion,
)
from app.models.operator_audit import OperatorAuditLog
from app.models.demo_session import DemoSession
from app.models.durable_job import DurableJob
from app.models.llm_routing_profile import LLMRoutingProfile
from app.models.document import Chunk, Document
from app.models.plugin import Matter
from app.models.research_workspace import (
    ResearchRecord,
    ResearchRecordRevision,
    ResearchWorkspace,
    ResearchWorkspaceEvent,
    ResearchWorkspaceIdempotency,
    ResearchWorkspaceMember,
    ResearchWorkspaceSnapshot,
)
from app.models.studio_draft import (
    StudioDraft,
    StudioDraftAuditEvent,
    StudioDraftField,
    StudioDraftIdempotency,
    StudioDraftPlacement,
    StudioDraftSnapshot,
    StudioSourceArtifact,
)
from app.models.studio_render import (
    StudioPreferredRenderEvidence,
    StudioRenderArtifact,
)
from app.models.tenant import Tenant, TenantSettings
from app.models.user import User
from app.schemas.studio_render import (
    StudioGeometryManifest,
    StudioPageGeometry,
    StudioRendererComponent,
    StudioRendererManifest,
    StudioRenderOptions,
    StudioRenderSourceContract,
    canonical_effective_render_request_hash,
    canonical_json_sha256,
    canonical_render_request_hash,
)
from app.services.demo_clone import clone_demo_fixture
from app.services.demo_purge import (
    DemoPurgeRefused,
    purge_demo_tenant,
    terminate_demo_tenant,
)
from app.services.studio_object_storage import LocalStudioObjectStore
from app.services.studio_render_jobs import (
    _PersistedResult,
    _QueuedPayload,
    _evidence_basis_sha256,
    _render_cache_key,
)
from app.services.demo_quota import (
    DemoQuotaExceeded,
    DemoReservation,
    release_demo_operation,
    reserve_demo_operation,
    settle_demo_operation,
)
from app.services import demo_clone, demo_purge
from app.routers import demo as demo_router


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
async def test_active_demo_resumes_by_email_without_extending_expiry_or_quota(
    db_session, monkeypatch
):
    fixture_id, tenant_id, user_id, session_id = (
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
    )
    expires_at = datetime.now(timezone.utc) + timedelta(hours=6)
    email = "resume@example.invalid"
    db_session.add_all(
        [
            _tenant(tenant_id=fixture_id, domain="resume-fixture.invalid"),
            _tenant(
                tenant_id=tenant_id,
                domain="resume.demo.invalid",
                billing_tier="demo",
                expires_at=expires_at,
            ),
        ]
    )
    await db_session.flush()
    await set_tenant_context(db_session, str(tenant_id))
    db_session.add_all(
        [
            User(
                id=user_id,
                tenant_id=tenant_id,
                email=email,
                full_name="Returning Prospect",
                role="admin",
                oauth_provider="demo",
                oauth_subject=str(session_id),
                is_active=True,
                license_active=True,
                premium_ai_enabled=False,
                privacy_mode=True,
            ),
            DemoSession(
                id=session_id,
                tenant_id=tenant_id,
                fixture_tenant_id=fixture_id,
                fixture_version="resume-test",
                prospect_name="Returning Prospect",
                prospect_email=email,
                status="active",
                quota=20,
                used=7,
                expires_at=expires_at,
            ),
        ]
    )
    await db_session.commit()

    monkeypatch.setattr(demo_router, "record_operator_audit", AsyncMock())
    monkeypatch.setattr(
        demo_router, "_issue_access_token", AsyncMock(return_value="access")
    )
    monkeypatch.setattr(
        demo_router, "_create_refresh_token", AsyncMock(return_value="refresh")
    )
    set_cookies = Mock()
    monkeypatch.setattr(demo_router, "_set_auth_cookies", set_cookies)
    response = Response()

    resumed = await demo_router._resume_active_demo_session(
        db_session,
        email=email,
        now=datetime.now(timezone.utc),
        request=Mock(),
        response=response,
    )

    assert resumed is not None
    assert resumed.resumed is True
    assert resumed.tenant_id == str(tenant_id)
    assert resumed.session_id == str(session_id)
    assert resumed.expires_at == expires_at
    assert resumed.quota == 20
    assert resumed.used == 7
    assert response.status_code == 200
    set_cookies.assert_called_once_with(response, "access", "refresh")


@pytest.mark.asyncio
async def test_new_demos_use_only_an_active_matter_aware_demo_profile(db_session):
    profile = LLMRoutingProfile(
        name="Demo Standard",
        standard_route={"model": "gpt-standard"},
        premium_route={"model": "gemini-premium"},
        standard_allow_matter_context=True,
        premium_allow_matter_context=True,
        is_demo_default=True,
        is_active=True,
        activation={
            "status": "active",
            "aliases": {
                "standard": "clarity-standard-rdemo",
                "premium": "clarity-premium-rdemo",
            },
        },
    )
    db_session.add(profile)
    await db_session.commit()

    selected = await demo_router._active_demo_routing_profile(db_session)

    assert selected is not None
    assert selected.id == profile.id


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
    fixture_id, tenant_id, user_id, demo_session_id = (
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
    )
    settings = demo_purge.get_settings()
    studio_storage_dir = tmp_path / "studio-render-cas"
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(
        settings, "TEMPLATE_STUDIO_RENDER_STORAGE_DIR", str(studio_storage_dir)
    )
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
                id=demo_session_id,
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
    matter_id, workspace_id, record_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db_session.add(
        Matter(
            id=matter_id,
            tenant_id=tenant_id,
            user_id=user_id,
            slug="purge-research",
            matter_name="Synthetic research purge",
        )
    )
    await db_session.flush()
    db_session.add_all(
        [
            ResearchWorkspace(
                id=workspace_id,
                tenant_id=tenant_id,
                matter_id=matter_id,
                title="Disposable research",
                created_by_user_id=user_id,
            ),
            ResearchWorkspaceMember(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                user_id=user_id,
                role="owner",
            ),
        ]
    )
    await db_session.flush()
    db_session.add_all(
        [
            ResearchRecord(
                id=record_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                record_type="memo",
                title="Disposable research record",
                evidence_class="model",
            ),
            ResearchWorkspaceEvent(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                record_id=record_id,
                actor_user_id=user_id,
                action="fixture_history",
                detail={"before": {}, "after": {}},
            ),
            ResearchWorkspaceSnapshot(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                sequence=1,
                sha256="a" * 64,
                payload={},
                created_by_user_id=user_id,
            ),
            ResearchRecordRevision(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                record_id=record_id,
                revision=1,
                actor_user_id=user_id,
                payload={"revision": 1},
            ),
            ResearchWorkspaceIdempotency(
                tenant_id=tenant_id,
                actor_user_id=user_id,
                operation="workspace_create",
                idempotency_key="fixture-research-workspace",
                request_sha256="b" * 64,
            ),
        ]
    )
    source_id, draft_id, field_id, snapshot_id, render_job_id, render_artifact_id = (
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
    )
    source_content = b"Disposable demo Studio source"
    source_sha256 = hashlib.sha256(source_content).hexdigest()
    source_contract = StudioRenderSourceContract(
        artifact_id=source_id,
        sha256=source_sha256,
        media_type="text/markdown",
        format="markdown",
    )
    snapshot_payload = {
        "contract_version": 1,
        "draft_id": str(draft_id),
        "revision": 1,
        "identity_sha256": "c" * 64,
        "format": "markdown",
        "lifecycle_state": "active",
        "source": source_contract.model_dump(mode="json"),
        "fields": [],
        "placements": [],
    }
    snapshot_sha256 = canonical_json_sha256(snapshot_payload)
    render_ref = LocalStudioObjectStore(studio_storage_dir, max_object_bytes=1024).put(
        tenant_id, b"rendered", media_type="application/pdf"
    )
    render_tenant_dir = studio_storage_dir / str(tenant_id)
    assert render_tenant_dir.is_dir()

    def component(name, digest):
        return StudioRendererComponent(
            name=name,
            version="1.0.0",
            content_sha256=digest * 64,
        )

    renderer_manifest = StudioRendererManifest(
        isolation_policy_id="studio-demo-purge-v1",
        launcher_sha256="1" * 64,
        sandbox_policy_sha256="2" * 64,
        fixed_arguments_sha256="3" * 64,
        environment_sha256="4" * 64,
        runtime_bundle_sha256="5" * 64,
        font_pack_sha256="6" * 64,
        renderer=component("renderer", "7"),
        rasterizer=component("rasterizer", "8"),
        converter=component("converter", "9"),
        validator=component("validator", "a"),
    )
    render_options = StudioRenderOptions()
    request_sha256 = canonical_render_request_hash(
        kind="studio_test_render",
        draft_id=draft_id,
        expected_revision=1,
        identity_sha256="c" * 64,
        snapshot_id=snapshot_id,
        content_sha256=snapshot_sha256,
        source=source_contract,
        render_options=render_options,
        requested_by=user_id,
        input_binding_id=None,
    )
    effective_request_sha256 = canonical_effective_render_request_hash(
        request_sha256=request_sha256,
        input_binding_sha256=None,
        input_binding_version=None,
    )
    cache_key = _render_cache_key(
        kind="studio_test_render",
        draft_id=draft_id,
        rendered_revision=1,
        identity_sha256="c" * 64,
        snapshot_id=snapshot_id,
        snapshot_content_sha256=snapshot_sha256,
        source=source_contract,
        render_options_sha256=render_options.sha256,
        effective_request_sha256=effective_request_sha256,
        input_binding_id=None,
        input_binding_sha256=None,
        input_binding_version=None,
        runtime_manifest_sha256=renderer_manifest.sha256,
    )
    queued_payload = _QueuedPayload(
        kind="studio_test_render",
        draft_id=draft_id,
        rendered_revision=1,
        identity_sha256="c" * 64,
        snapshot_id=snapshot_id,
        snapshot_content_sha256=snapshot_sha256,
        source=source_contract,
        render_options=render_options,
        render_options_sha256=render_options.sha256,
        request_sha256=request_sha256,
        effective_request_sha256=effective_request_sha256,
        requested_by=user_id,
        renderer_manifest=renderer_manifest,
        runtime_manifest_sha256=renderer_manifest.sha256,
        cache_key=cache_key,
        admission_bytes=render_options.max_output_bytes + len(source_content),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    geometry_manifest = StudioGeometryManifest(
        artifact_page_count=1,
        document_page_count=1,
        pages=[StudioPageGeometry(page_number=1, coordinate_space="none")],
    )
    persisted_result = _PersistedResult(
        artifact_id=render_artifact_id,
        adoption_outcome="current_evidence",
        preferred_evidence_at_completion=True,
        retention_class="evidence",
        output_sha256=render_ref.sha256,
        media_type=render_ref.media_type,
        byte_size=render_ref.byte_size,
        artifact_page_count=1,
        document_page_count=1,
        geometry_manifest_sha256=geometry_manifest.sha256,
    )
    evidence_basis_sha256 = _evidence_basis_sha256(queued_payload)
    db_session.add(
        StudioSourceArtifact(
            id=source_id,
            tenant_id=tenant_id,
            sha256=source_sha256,
            media_type="text/markdown",
            format="markdown",
            byte_size=len(source_content),
            resolver_key=f"studio-db:v1:{uuid.uuid4()}",
            content_bytes=source_content,
            created_by_user_id=user_id,
        )
    )
    await db_session.flush()
    db_session.add(
        StudioDraft(
            id=draft_id,
            tenant_id=tenant_id,
            source_artifact_id=source_id,
            source_sha256=source_sha256,
            source_media_type="text/markdown",
            title="Disposable Studio draft",
            format="markdown",
            identity_sha256="c" * 64,
            created_by_user_id=user_id,
            updated_by_user_id=user_id,
        )
    )
    await db_session.flush()
    db_session.add(
        StudioDraftField(
            id=field_id,
            tenant_id=tenant_id,
            draft_id=draft_id,
            automation_key="client_name",
            label="Client name",
            field_type="text",
        )
    )
    await db_session.flush()
    db_session.add_all(
        [
            StudioDraftPlacement(
                tenant_id=tenant_id,
                draft_id=draft_id,
                field_id=field_id,
                format="markdown",
                anchor_kind="template_token",
                anchor={"token": "client_name"},
            ),
            StudioDraftSnapshot(
                id=snapshot_id,
                tenant_id=tenant_id,
                draft_id=draft_id,
                source_artifact_id=source_id,
                revision=1,
                identity_sha256="c" * 64,
                content_sha256=snapshot_sha256,
                payload=snapshot_payload,
                created_by_user_id=user_id,
            ),
            StudioDraftIdempotency(
                tenant_id=tenant_id,
                actor_user_id=user_id,
                operation="create",
                idempotency_key="demo-studio-create",
                request_sha256="e" * 64,
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            ),
            StudioDraftAuditEvent(
                tenant_id=tenant_id,
                draft_id=draft_id,
                event_type="created",
                revision=1,
                actor_user_id=user_id,
                detail={},
            ),
        ]
    )
    await db_session.flush()
    db_session.add(
        DurableJob(
            id=render_job_id,
            tenant_id=tenant_id,
            kind=queued_payload.kind,
            idempotency_key=f"studio-render:{'b' * 64}",
            payload=queued_payload.model_dump(mode="json", exclude_none=True),
            status="completed",
            progress=100,
            attempts=1,
            result=persisted_result.model_dump(mode="json", exclude_none=True),
            completed_at=datetime.now(timezone.utc),
        )
    )
    await db_session.flush()
    db_session.add(
        StudioRenderArtifact(
            id=render_artifact_id,
            tenant_id=tenant_id,
            job_id=render_job_id,
            draft_id=draft_id,
            snapshot_id=snapshot_id,
            source_artifact_id=source_id,
            requested_by_user_id=user_id,
            revision=1,
            identity_sha256="c" * 64,
            evidence_basis_sha256=evidence_basis_sha256,
            snapshot_content_sha256=snapshot_sha256,
            source_sha256=source_sha256,
            source_media_type="text/markdown",
            source_format="markdown",
            request_sha256=queued_payload.request_sha256,
            effective_request_sha256=queued_payload.effective_request_sha256,
            render_options=render_options.model_dump(mode="json"),
            render_options_sha256=render_options.sha256,
            cache_key=queued_payload.cache_key,
            artifact_kind="test_render",
            content_sha256=render_ref.sha256,
            byte_size=render_ref.byte_size,
            media_type=render_ref.media_type,
            object_key=render_ref.object_key,
            runtime_manifest=renderer_manifest.model_dump(mode="json"),
            runtime_manifest_sha256=renderer_manifest.sha256,
            artifact_page_count=1,
            document_page_count=1,
            geometry_manifest=geometry_manifest.model_dump(mode="json"),
            geometry_manifest_sha256=geometry_manifest.sha256,
            adoption_outcome="current_evidence",
            retention_class="evidence",
        )
    )
    await db_session.flush()
    db_session.add(
        StudioPreferredRenderEvidence(
            tenant_id=tenant_id,
            draft_id=draft_id,
            evidence_basis_sha256=evidence_basis_sha256,
            artifact_id=render_artifact_id,
            job_id=render_job_id,
            revision=1,
            identity_sha256="c" * 64,
        )
    )
    contact_id = uuid.uuid4()
    matter_field_id, contact_field_id = uuid.uuid4(), uuid.uuid4()
    template_id, version_id, run_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db_session.add(
        Contact(
            id=contact_id,
            tenant_id=tenant_id,
            first_name="Disposable",
            last_name="Workflow contact",
            created_by_user_id=user_id,
        )
    )
    db_session.add_all(
        [
            CustomFieldDefinition(
                id=matter_field_id,
                tenant_id=tenant_id,
                entity_type="matter",
                field_key="demo_matter_field",
                label="Demo matter field",
                field_type="text",
                created_by_user_id=user_id,
            ),
            CustomFieldDefinition(
                id=contact_field_id,
                tenant_id=tenant_id,
                entity_type="contact",
                field_key="demo_contact_field",
                label="Demo contact field",
                field_type="text",
                created_by_user_id=user_id,
            ),
            MatterWorkflowTemplate(
                id=template_id,
                tenant_id=tenant_id,
                name="Disposable approved workflow",
                created_by_user_id=user_id,
            ),
        ]
    )
    await db_session.flush()
    workflow_version = MatterWorkflowTemplateVersion(
        id=version_id,
        tenant_id=tenant_id,
        template_id=template_id,
        version=1,
        status="draft",
        initial_stage_key="initial",
        definition_sha256="a" * 64,
        created_by_user_id=user_id,
    )
    db_session.add(workflow_version)
    await db_session.flush()
    db_session.add_all(
        [
            MatterCustomFieldValue(
                tenant_id=tenant_id,
                matter_id=matter_id,
                field_definition_id=matter_field_id,
                value_json="demo",
                value_hmac="b" * 64,
                updated_by_user_id=user_id,
            ),
            ContactCustomFieldValue(
                tenant_id=tenant_id,
                contact_id=contact_id,
                field_definition_id=contact_field_id,
                value_json="demo",
                value_hmac="c" * 64,
                updated_by_user_id=user_id,
            ),
            MatterWorkflowStageDefinition(
                tenant_id=tenant_id,
                template_version_id=version_id,
                stage_key="initial",
                label="Initial",
                position=0,
            ),
            MatterWorkflowFieldRequirement(
                tenant_id=tenant_id,
                template_version_id=version_id,
                field_definition_id=matter_field_id,
            ),
        ]
    )
    await db_session.flush()
    db_session.add(
        MatterWorkflowChecklistDefinition(
            tenant_id=tenant_id,
            template_version_id=version_id,
            stage_key="initial",
            item_key="review",
            title="Review demo file",
            position=0,
            task_type="review",
            priority="medium",
            due_offset_days=1,
            assignee_role="matter_owner",
        )
    )
    await db_session.flush()
    workflow_version.status = "approved"
    workflow_version.approved_by_user_id = user_id
    workflow_version.approved_at = datetime.now(timezone.utc)
    await db_session.flush()
    db_session.add(
        MatterWorkflowRun(
            id=run_id,
            tenant_id=tenant_id,
            matter_id=matter_id,
            template_version_id=version_id,
            idempotency_key="demo-applied-workflow",
            request_sha256="d" * 64,
            template_sha256="a" * 64,
            matter_sha256="e" * 64,
            preview_sha256="f" * 64,
            preview_json={"initial_stage": {"label": "Initial"}, "tasks": []},
            status="applied",
            prior_stage="New",
            planned_by_user_id=user_id,
            approved_by_user_id=user_id,
            approved_at=datetime.now(timezone.utc),
        )
    )
    await db_session.flush()
    db_session.add_all(
        [
            MatterWorkflowRunEvent(
                tenant_id=tenant_id,
                run_id=run_id,
                sequence=1,
                event_type="applied",
                actor_user_id=user_id,
                evidence_sha256="1" * 64,
            ),
            MatterWorkflowRunStep(
                tenant_id=tenant_id,
                run_id=run_id,
                sequence=1,
                step_type="matter_stage",
                action_key="initial",
                status="succeeded",
                evidence_sha256="2" * 64,
            ),
        ]
    )
    await db_session.commit()
    # Matching user-settable context is still insufficient while this is an
    # active demo session. The DB trigger requires the service's claimed,
    # inactive, expired lifecycle facts before it allows immutable DELETE.
    await db_session.execute(
        text(
            "SELECT set_config('app.research_workspace_demo_purge_tenant_id', :value, true)"
        ),
        {"value": str(tenant_id)},
    )
    await db_session.execute(
        text(
            "SELECT set_config('app.research_workspace_demo_purge_session_id', :value, true)"
        ),
        {"value": str(demo_session_id)},
    )
    with pytest.raises(
        Exception, match="research history, snapshots, and revisions are immutable"
    ):
        await db_session.execute(
            text(
                "DELETE FROM research_workspace_snapshots WHERE tenant_id = :tenant_id"
            ),
            {"tenant_id": str(tenant_id)},
        )
    await db_session.rollback()
    # A stale or other session context is also refused even when the tenant is
    # otherwise an expired, inactive demo with a purging claim. The service
    # below reclaims this old claim and supplies its exact session context.
    await set_tenant_context(db_session, str(tenant_id))
    claimed_tenant = await db_session.get(Tenant, tenant_id)
    claimed_demo = await db_session.get(DemoSession, demo_session_id)
    claimed_tenant.is_active = False
    claimed_demo.status = "purging"
    claimed_demo.purge_started_at = datetime.now(timezone.utc) - (
        demo_purge._PURGE_RECLAIM_AFTER + timedelta(minutes=1)
    )
    await db_session.commit()
    await set_tenant_context(db_session, str(tenant_id))
    await db_session.execute(
        text(
            "SELECT set_config('app.research_workspace_demo_purge_tenant_id', :value, true)"
        ),
        {"value": str(tenant_id)},
    )
    await db_session.execute(
        text(
            "SELECT set_config('app.research_workspace_demo_purge_session_id', :value, true)"
        ),
        {"value": str(uuid.uuid4())},
    )
    with pytest.raises(
        Exception, match="research history, snapshots, and revisions are immutable"
    ):
        await db_session.execute(
            text("DELETE FROM research_record_revisions WHERE tenant_id = :tenant_id"),
            {"tenant_id": str(tenant_id)},
        )
    await db_session.rollback()
    target_file = tmp_path / str(tenant_id) / "document.txt"
    target_file.parent.mkdir(parents=True)
    target_file.write_text("disposable", encoding="utf-8")

    deleted = await purge_demo_tenant(db_session, tenant_id)

    assert await db_session.scalar(select(Tenant.id).where(Tenant.id == fixture_id))
    assert (
        await db_session.scalar(select(Tenant.id).where(Tenant.id == tenant_id)) is None
    )
    assert deleted["research_workspaces"] == 1
    assert deleted["research_records"] == 1
    assert deleted["research_workspace_events"] == 1
    assert deleted["research_workspace_snapshots"] == 1
    assert deleted["research_record_revisions"] == 1
    assert deleted["research_workspace_idempotency"] == 1
    assert deleted["research_workspace_members"] == 1
    for studio_table in demo_purge._STUDIO_PURGE_ORDER:
        assert deleted[studio_table] == 1
    assert deleted["durable_jobs"] == 1
    expected_workflow_counts = {
        "custom_field_definitions": 2,
        "matter_custom_field_values": 1,
        "contact_custom_field_values": 1,
        "matter_workflow_templates": 1,
        "matter_workflow_template_versions": 1,
        "matter_workflow_stage_definitions": 1,
        "matter_workflow_checklist_definitions": 1,
        "matter_workflow_field_requirements": 1,
        "matter_workflow_runs": 1,
        "matter_workflow_run_events": 1,
        "matter_workflow_run_steps": 1,
    }
    for workflow_table, expected_count in expected_workflow_counts.items():
        assert deleted[workflow_table] == expected_count
    assert await db_session.scalar(
        select(OperatorAuditLog.id).where(
            OperatorAuditLog.action == "demo.session.purged"
        )
    )
    assert not target_file.exists()
    assert not render_tenant_dir.exists()


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


def test_remove_studio_render_files_refuses_containment_failure(monkeypatch):
    from app.services import demo_purge

    tenant_id = uuid.uuid4()
    storage_dir = "/tmp/studio-render-storage"
    monkeypatch.setattr(
        demo_purge.get_settings(),
        "TEMPLATE_STUDIO_RENDER_STORAGE_DIR",
        storage_dir,
    )

    class _FakePath:
        def __init__(self, path):
            self._path = str(path)

        def strip(self):
            return self._path.strip()

        def resolve(self):
            return _FakePath(self._path)

        def __truediv__(self, other):
            return _FakePath(self._path + "/" + str(other))

        def is_relative_to(self, other):
            return False

        def exists(self):
            return False

    monkeypatch.setattr(demo_purge, "Path", _FakePath)

    with pytest.raises(DemoPurgeRefused, match="render storage path failed"):
        demo_purge._remove_studio_render_files(tenant_id)


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


@pytest.mark.asyncio
async def test_operator_termination_purges_only_the_selected_active_demo(
    db_session, tmp_path, monkeypatch
):
    fixture_id = uuid.uuid4()
    target_id = uuid.uuid4()
    other_id = uuid.uuid4()
    target_session_id = uuid.uuid4()
    other_session_id = uuid.uuid4()
    expires_at = datetime.now(timezone.utc) + timedelta(hours=2)
    monkeypatch.setattr(demo_purge.get_settings(), "UPLOAD_DIR", str(tmp_path))

    db_session.add_all(
        [
            _tenant(tenant_id=fixture_id, domain="operator-fixture.invalid"),
            _tenant(
                tenant_id=target_id,
                domain="operator-target.demo.invalid",
                billing_tier="demo",
                expires_at=expires_at,
            ),
            _tenant(
                tenant_id=other_id,
                domain="ongoing-demo.demo.invalid",
                billing_tier="demo",
                expires_at=expires_at,
            ),
        ]
    )
    await db_session.flush()
    db_session.add_all(
        [
            DemoSession(
                id=target_session_id,
                tenant_id=target_id,
                fixture_tenant_id=fixture_id,
                fixture_version="operator-test",
                prospect_name="Completed Demo",
                prospect_email="completed@example.invalid",
                status="active",
                quota=20,
                expires_at=expires_at,
            ),
            DemoSession(
                id=other_session_id,
                tenant_id=other_id,
                fixture_tenant_id=fixture_id,
                fixture_version="operator-test",
                prospect_name="Ongoing Demo",
                prospect_email="ongoing@example.invalid",
                status="active",
                quota=20,
                expires_at=expires_at,
            ),
        ]
    )
    await db_session.commit()

    await terminate_demo_tenant(
        db_session,
        target_id,
        target_session_id,
        actor_id="demo-operator",
        reason="Demo completed",
    )

    assert await db_session.get(Tenant, target_id) is None
    other = await db_session.get(Tenant, other_id)
    assert other is not None
    assert other.is_active is True
    assert other.expires_at == expires_at

    await set_tenant_context(db_session, str(other_id))
    other_session = await db_session.scalar(
        select(DemoSession).where(DemoSession.id == other_session_id)
    )
    assert other_session is not None
    assert other_session.status == "active"

    audit = await db_session.scalar(
        select(OperatorAuditLog).where(
            OperatorAuditLog.action == "demo.session.purged",
            OperatorAuditLog.resource_id == str(target_session_id),
        )
    )
    assert audit.actor_type == "platform_operator"
    assert audit.actor_id == "demo-operator"
    assert audit.metadata_json["reason"] == "Demo completed"
