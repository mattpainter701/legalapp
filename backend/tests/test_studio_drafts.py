"""Focused contract tests for the Template Studio draft foundation."""

import asyncio
import hashlib
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.document_template import DocumentTemplate
from app.models.rbac import Role, UserRole
from app.models.studio_draft import (
    StudioDraft,
    StudioDraftPlacement,
    StudioDraftSnapshot,
    StudioSourceArtifact,
)
from app.schemas.studio_draft import StudioDraftCreate, StudioDraftPatch
from app.services.studio_drafts import StudioDraftService, StudioError

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def _grant_manage_documents(db_session, test_tenant, test_user):
    role = Role(
        tenant_id=test_tenant.id,
        name="Studio document managers",
        capabilities=["manage_documents"],
    )
    db_session.add(role)
    await db_session.flush()
    db_session.add(
        UserRole(
            tenant_id=test_tenant.id,
            user_id=test_user.id,
            role_id=role.id,
            source="manual",
        )
    )
    await db_session.commit()


async def _register_source(
    client,
    content=b"studio source",
    media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
):
    response = await client.post(
        "/api/template-studio/drafts/sources",
        files={"source": ("template.bin", content, media_type)},
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert set(payload) == {"contract_version", "artifact_id", "sha256", "media_type"}
    assert payload["sha256"] == hashlib.sha256(content).hexdigest()
    return payload


def _create_payload(source_artifact_id):
    return {
        "title": "Engagement letter",
        "format": "docx",
        "source_artifact_id": str(source_artifact_id),
        "fields": [
            {
                "client_key": "client-name",
                "automation_key": "client.name",
                "label": "Client name",
                "field_type": "text",
                "required": True,
                "position": 0,
                "definition": {"max_length": 200},
            }
        ],
        "placements": [
            {
                "client_key": "client-name-header",
                "field_client_key": "client-name",
                "format": "docx",
                "anchor_kind": "content_control",
                "anchor": {"tag": "client-name-header"},
            },
            {
                "client_key": "client-name-signature",
                "field_client_key": "client-name",
                "format": "docx",
                "anchor_kind": "content_control",
                "anchor": {"tag": "client-name-signature"},
            },
        ],
    }


async def test_stable_field_identity_multiple_placements_and_stale_write(client):
    source = await _register_source(client)
    created = await client.post(
        "/api/template-studio/drafts",
        json=_create_payload(source["artifact_id"]),
        headers={"Idempotency-Key": "create-stable-field"},
    )
    assert created.status_code == 201, created.text
    original = created.json()
    field_id = original["fields"][0]["id"]
    assert original["revision"] == 1
    assert original["fields"][0]["id"] == str(field_id)
    assert len(original["placements"]) == 2
    assert created.headers["etag"] == original["etag"]

    renamed_field = dict(original["fields"][0])
    renamed_field.pop("definition")
    renamed_field["definition"] = {"max_length": 200}
    renamed_field["automation_key"] = "client.legal_name"
    patched = await client.patch(
        f"/api/template-studio/drafts/{original['id']}",
        json={
            "base_revision": 1,
            "operations": [{"op": "upsert_field", "field": renamed_field}],
        },
        headers={"Idempotency-Key": "rename-stable-field"},
    )
    assert patched.status_code == 200, patched.text
    current = patched.json()
    assert current["revision"] == 2
    assert current["fields"][0]["id"] == str(field_id)
    assert current["fields"][0]["automation_key"] == "client.legal_name"
    assert current["identity_sha256"] != original["identity_sha256"]

    stale = await client.patch(
        f"/api/template-studio/drafts/{original['id']}",
        json={
            "base_revision": 1,
            "operations": [{"op": "set_metadata", "title": "Lost update"}],
        },
        headers={"Idempotency-Key": "stale-write-test"},
    )
    assert stale.status_code == 409
    detail = stale.json()["detail"]
    assert detail["code"] == "stale_revision"
    assert detail["expected_revision"] == 1
    assert detail["current_revision"] == 2


async def test_idempotency_mismatch_source_identity_and_payload_bounds(client):
    source = await _register_source(client)
    payload = _create_payload(source["artifact_id"])
    first = await client.post(
        "/api/template-studio/drafts",
        json=payload,
        headers={"Idempotency-Key": "idempotency-create"},
    )
    assert first.status_code == 201, first.text
    replay = await client.post(
        "/api/template-studio/drafts",
        json=payload,
        headers={"Idempotency-Key": "idempotency-create"},
    )
    assert replay.status_code == 201
    assert replay.json()["id"] == first.json()["id"]

    changed = dict(payload)
    changed["title"] = "Different request"
    mismatch = await client.post(
        "/api/template-studio/drafts",
        json=changed,
        headers={"Idempotency-Key": "idempotency-create"},
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["detail"]["code"] == "idempotency_key_mismatch"

    source_mismatch = await client.patch(
        f"/api/template-studio/drafts/{first.json()['id']}",
        json={
            "base_revision": 1,
            "operations": [
                {
                    "op": "replace_source",
                    "source_artifact_id": str(uuid.uuid4()),
                }
            ],
        },
        headers={"Idempotency-Key": "source-mismatch"},
    )
    assert source_mismatch.status_code == 404

    unsafe = _create_payload(source["artifact_id"])
    unsafe["fields"][0]["definition"] = {"default": "privileged client value"}
    rejected = await client.post(
        "/api/template-studio/drafts",
        json=unsafe,
        headers={"Idempotency-Key": "unsafe-durable-payload"},
    )
    assert rejected.status_code == 422
    assert rejected.json()["detail"]["code"] == "unsupported_field_definition_key"

    too_many = _create_payload(source["artifact_id"])
    too_many["fields"] = [too_many["fields"][0]] * 201
    too_many["placements"] = []
    bounded = await client.post(
        "/api/template-studio/drafts",
        json=too_many,
        headers={"Idempotency-Key": "field-count-bound"},
    )
    assert bounded.status_code == 422


async def test_registered_source_reader_rechecks_exact_bytes(
    db_session, test_tenant, test_user
):
    service = StudioDraftService(db_session, test_tenant.id, test_user.id)
    registered = await service.register_source(b"trusted bytes", "text/markdown")
    replay = await service.register_source(b"trusted bytes", "text/markdown")
    assert replay["artifact_id"] == registered["artifact_id"]
    assert (
        await service.read_source_bytes(registered["artifact_id"]) == b"trusted bytes"
    )

    artifact = await db_session.get(
        StudioSourceArtifact, uuid.UUID(str(registered["artifact_id"]))
    )
    artifact.content_bytes = b"tampered bytes"
    with db_session.no_autoflush, pytest.raises(StudioError) as caught:
        await service.read_source_bytes(registered["artifact_id"])
    assert caught.value.status_code == 409
    assert caught.value.detail["code"] == "source_integrity_failed"
    await db_session.rollback()


async def test_leaked_field_and_placement_ids_match_nonexistent_behavior(client):
    source = await _register_source(client)
    drafts = []
    for suffix in ("a", "b"):
        response = await client.post(
            "/api/template-studio/drafts",
            json={**_create_payload(source["artifact_id"]), "title": f"Draft {suffix}"},
            headers={"Idempotency-Key": f"id-oracle-create-{suffix}"},
        )
        assert response.status_code == 201, response.text
        drafts.append(response.json())

    target, foreign = drafts
    for candidate in (foreign["fields"][0]["id"], str(uuid.uuid4())):
        payload = dict(target["fields"][0])
        payload["id"] = candidate
        response = await client.patch(
            f"/api/template-studio/drafts/{target['id']}",
            json={
                "base_revision": 1,
                "operations": [{"op": "upsert_field", "field": payload}],
            },
            headers={"Idempotency-Key": f"field-oracle-{candidate}"},
        )
        assert response.status_code == 404
        assert response.json()["detail"] == "Draft field not found"

    for candidate in (foreign["placements"][0]["id"], str(uuid.uuid4())):
        payload = dict(target["placements"][0])
        payload["id"] = candidate
        response = await client.patch(
            f"/api/template-studio/drafts/{target['id']}",
            json={
                "base_revision": 1,
                "operations": [{"op": "upsert_placement", "placement": payload}],
            },
            headers={"Idempotency-Key": f"placement-oracle-{candidate}"},
        )
        assert response.status_code == 404
        assert response.json()["detail"] == "Draft placement not found"


async def test_snapshot_hash_immutability_archive_cancel_and_evidence_recheck(
    client, db_session, test_tenant, test_user
):
    source = await _register_source(client)
    created = await client.post(
        "/api/template-studio/drafts",
        json=_create_payload(source["artifact_id"]),
        headers={"Idempotency-Key": "snapshot-create"},
    )
    draft = created.json()
    snapshot_response = await client.post(
        f"/api/template-studio/drafts/{draft['id']}/snapshots",
        json={"expected_revision": 1},
        headers={"Idempotency-Key": "snapshot-current"},
    )
    assert snapshot_response.status_code == 201, snapshot_response.text
    snapshot = snapshot_response.json()
    canonical = (
        __import__("json")
        .dumps(
            snapshot["payload"],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        .encode()
    )
    assert hashlib.sha256(canonical).hexdigest() == snapshot["content_sha256"]
    durable = str(snapshot["payload"]).lower()
    for forbidden in (
        "privileged client value",
        "storage_path",
        "signed_url",
        "provider_id",
    ):
        assert forbidden not in durable

    row = await db_session.get(StudioDraftSnapshot, uuid.UUID(snapshot["id"]))
    original_hash = row.content_sha256
    snapshot["payload"]["format"] = "client-side mutation"
    await db_session.refresh(row)
    assert row.content_sha256 == original_hash

    service = StudioDraftService(db_session, test_tenant.id, test_user.id)
    assert (
        await service.mark_render_evidence_if_current(
            uuid.UUID(draft["id"]), 1, draft["identity_sha256"]
        )
        is True
    )

    archived = await client.patch(
        f"/api/template-studio/drafts/{draft['id']}",
        json={
            "base_revision": 1,
            "operations": [{"op": "archive"}, {"op": "request_cancel"}],
        },
        headers={"Idempotency-Key": "archive-and-cancel"},
    )
    assert archived.status_code == 200, archived.text
    result = archived.json()
    assert result["revision"] == 2
    assert result["lifecycle_state"] == "archived"
    assert result["cancellation_requested"] is True
    assert result["evidence_invalidated"] is True
    assert (
        await service.mark_render_evidence_if_current(
            uuid.UUID(draft["id"]), 1, draft["identity_sha256"]
        )
        is False
    )


async def test_published_template_import_and_safe_compatibility_promote(
    client, db_session, test_tenant
):
    body = "Dear {{client_name}}"
    template = DocumentTemplate(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        title="Existing",
        body=body,
        format="markdown",
        status="draft",
        source_sha256=hashlib.sha256(body.encode()).hexdigest(),
        source_content_type="text/markdown",
        variable_schema={
            "version": 1,
            "fields": [
                {
                    "name": "client_name",
                    "label": "Client",
                    "type": "text",
                    "required": True,
                }
            ],
        },
    )
    db_session.add(template)
    await db_session.commit()

    imported = await client.post(
        "/api/template-studio/drafts/imports",
        json={"template_id": str(template.id)},
        headers={"Idempotency-Key": "import-published-template"},
    )
    assert imported.status_code == 201, imported.text
    draft = imported.json()
    field_id = draft["fields"][0]["id"]
    promoted = await client.post(
        f"/api/template-studio/drafts/{draft['id']}/promote",
        json={"expected_revision": 1, "status": "draft"},
        headers={"Idempotency-Key": "promote-compatibility"},
    )
    assert promoted.status_code == 200, promoted.text
    await db_session.refresh(template)
    assert template.status == "draft"
    assert template.variable_schema["version"] == 2
    assert template.variable_schema["fields"][0]["studio_field_id"] == field_id
    assert template.body == body


async def test_promotion_rejects_concurrent_compatibility_edit_and_invalid_state(
    client, db_session, test_tenant
):
    body = "Hello {{name}}"
    template = DocumentTemplate(
        tenant_id=test_tenant.id,
        title="Compatibility base",
        body=body,
        format="markdown",
        status="draft",
        source_sha256=hashlib.sha256(body.encode()).hexdigest(),
        source_content_type="text/markdown",
        variable_schema={"fields": [{"name": "name", "type": "text"}]},
    )
    db_session.add(template)
    await db_session.commit()
    imported = await client.post(
        "/api/template-studio/drafts/imports",
        json={"template_id": str(template.id)},
        headers={"Idempotency-Key": "promotion-stale-import"},
    )
    assert imported.status_code == 201, imported.text
    draft = imported.json()

    template.title = "Concurrent editor changed this"
    await db_session.commit()
    stale = await client.post(
        f"/api/template-studio/drafts/{draft['id']}/promote",
        json={"expected_revision": 1, "status": "draft"},
        headers={"Idempotency-Key": "promotion-stale-check"},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "stale_published_template"

    invalid_active = await client.post(
        f"/api/template-studio/drafts/{draft['id']}/promote",
        json={"expected_revision": 1, "status": "active"},
        headers={"Idempotency-Key": "promotion-active-rejected"},
    )
    assert invalid_active.status_code == 422


async def test_promotion_revalidates_persisted_placement_contract(
    client, db_session, test_tenant
):
    body = "Hello {{name}}"
    template = DocumentTemplate(
        tenant_id=test_tenant.id,
        title="Invalid placement base",
        body=body,
        format="markdown",
        status="draft",
        source_sha256=hashlib.sha256(body.encode()).hexdigest(),
        source_content_type="text/markdown",
        variable_schema={"fields": [{"name": "name", "type": "text"}]},
    )
    db_session.add(template)
    await db_session.commit()
    imported = await client.post(
        "/api/template-studio/drafts/imports",
        json={"template_id": str(template.id)},
        headers={"Idempotency-Key": "promotion-invalid-import"},
    )
    draft = imported.json()
    placement = await db_session.get(
        StudioDraftPlacement, uuid.UUID(draft["placements"][0]["id"])
    )
    placement.anchor = {"token": "name", "value": "smuggled"}
    await db_session.commit()

    promoted = await client.post(
        f"/api/template-studio/drafts/{draft['id']}/promote",
        json={"expected_revision": 1, "status": "draft"},
        headers={"Idempotency-Key": "promotion-invalid-check"},
    )
    assert promoted.status_code == 422
    assert promoted.json()["detail"]["code"] == "draft_validation_failed"


async def test_active_quota_is_atomic_for_concurrent_create_and_restore(
    db_session, test_engine, test_tenant, test_user, monkeypatch
):
    bootstrap = StudioDraftService(db_session, test_tenant.id, test_user.id)
    source = await bootstrap.register_source(
        b"quota source",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    monkeypatch.setattr(bootstrap.settings, "TEMPLATE_STUDIO_ACTIVE_DRAFT_QUOTA", 1)
    create_request = StudioDraftCreate.model_validate(
        _create_payload(source["artifact_id"])
    )
    factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async def create_one(key):
        async with factory() as session:
            service = StudioDraftService(session, test_tenant.id, test_user.id)
            try:
                return await service.create(create_request, key)
            except StudioError as exc:
                await session.rollback()
                return exc

    results = await asyncio.gather(
        create_one("concurrent-quota-a"), create_one("concurrent-quota-b")
    )
    successes = [item for item in results if isinstance(item, dict)]
    failures = [item for item in results if isinstance(item, StudioError)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert failures[0].status_code == 429

    async with factory() as session:
        service = StudioDraftService(session, test_tenant.id, test_user.id)
        archived = await service.patch(
            uuid.UUID(str(successes[0]["id"])),
            StudioDraftPatch.model_validate(
                {"base_revision": 1, "operations": [{"op": "archive"}]}
            ),
            "quota-archive",
        )
        replacement = await service.create(create_request, "quota-replacement")
        with pytest.raises(StudioError) as restore_error:
            await service.patch(
                uuid.UUID(str(archived["id"])),
                StudioDraftPatch.model_validate(
                    {"base_revision": 2, "operations": [{"op": "restore"}]}
                ),
                "quota-restore",
            )
        assert restore_error.value.status_code == 429
        assert replacement["lifecycle_state"] == "active"


async def test_proposal_acceptance_seam_advances_exactly_one_revision(
    db_session, test_tenant, test_user
):
    service = StudioDraftService(db_session, test_tenant.id, test_user.id)
    # The Phase 4 seam uses the same bounded patch transaction with a distinct
    # operation name. The endpoint is intentionally not exposed in Phase 2.
    from app.schemas.studio_draft import StudioDraftCreate

    source = await service.register_source(
        b"proposal seam",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    draft = await service.create(
        StudioDraftCreate.model_validate(_create_payload(source["artifact_id"])),
        "proposal-seam-create",
    )
    patched = await service.patch(
        uuid.UUID(str(draft["id"])),
        StudioDraftPatch.model_validate(
            {
                "base_revision": 1,
                "operations": [{"op": "set_metadata", "title": "Accepted proposal"}],
            }
        ),
        "proposal-seam-accept",
        operation="accept_proposal",
    )
    assert patched["revision"] == 2
    row = await db_session.get(StudioDraft, uuid.UUID(str(draft["id"])))
    assert row.revision == 2
