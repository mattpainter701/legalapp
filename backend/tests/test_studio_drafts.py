"""Focused contract tests for the Template Studio draft foundation."""

import hashlib
import uuid

import pytest

from app.models.document_template import DocumentTemplate
from app.models.studio_draft import StudioDraft, StudioDraftSnapshot
from app.schemas.studio_draft import StudioDraftPatch
from app.services.studio_drafts import StudioDraftService

pytestmark = pytest.mark.asyncio


def _create_payload(field_id=None):
    field_id = field_id or uuid.uuid4()
    return {
        "title": "Engagement letter",
        "format": "docx",
        "source_artifact_id": str(uuid.uuid4()),
        "source_sha256": "a" * 64,
        "source_media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "fields": [{
            "id": str(field_id),
            "automation_key": "client.name",
            "label": "Client name",
            "field_type": "text",
            "required": True,
            "position": 0,
            "definition": {"max_length": 200},
        }],
        "placements": [
            {
                "id": str(uuid.uuid4()), "field_id": str(field_id),
                "format": "docx", "anchor_kind": "content_control",
                "anchor": {"tag": "client-name-header"},
            },
            {
                "id": str(uuid.uuid4()), "field_id": str(field_id),
                "format": "docx", "anchor_kind": "content_control",
                "anchor": {"tag": "client-name-signature"},
            },
        ],
    }


async def test_stable_field_identity_multiple_placements_and_stale_write(client):
    field_id = uuid.uuid4()
    created = await client.post(
        "/api/template-studio/drafts",
        json=_create_payload(field_id),
        headers={"Idempotency-Key": "create-stable-field"},
    )
    assert created.status_code == 201, created.text
    original = created.json()
    assert original["revision"] == 1
    assert original["fields"][0]["id"] == str(field_id)
    assert len(original["placements"]) == 2
    assert created.headers["etag"] == original["etag"]

    renamed_field = dict(_create_payload(field_id)["fields"][0])
    renamed_field["automation_key"] = "client.legal_name"
    patched = await client.patch(
        f"/api/template-studio/drafts/{original['id']}",
        json={"base_revision": 1, "operations": [{"op": "upsert_field", "field": renamed_field}]},
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
        json={"base_revision": 1, "operations": [{"op": "set_metadata", "title": "Lost update"}]},
        headers={"Idempotency-Key": "stale-write-test"},
    )
    assert stale.status_code == 409
    detail = stale.json()["detail"]
    assert detail["code"] == "stale_revision"
    assert detail["expected_revision"] == 1
    assert detail["current_revision"] == 2


async def test_idempotency_mismatch_source_identity_and_payload_bounds(client):
    payload = _create_payload()
    first = await client.post(
        "/api/template-studio/drafts", json=payload,
        headers={"Idempotency-Key": "idempotency-create"},
    )
    assert first.status_code == 201, first.text
    replay = await client.post(
        "/api/template-studio/drafts", json=payload,
        headers={"Idempotency-Key": "idempotency-create"},
    )
    assert replay.status_code == 201
    assert replay.json()["id"] == first.json()["id"]

    changed = dict(payload)
    changed["title"] = "Different request"
    mismatch = await client.post(
        "/api/template-studio/drafts", json=changed,
        headers={"Idempotency-Key": "idempotency-create"},
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["detail"]["code"] == "idempotency_key_mismatch"

    source_mismatch = await client.patch(
        f"/api/template-studio/drafts/{first.json()['id']}",
        json={
            "base_revision": 1,
            "operations": [{
                "op": "replace_source",
                "source_artifact_id": payload["source_artifact_id"],
                "source_sha256": "b" * 64,
                "source_media_type": payload["source_media_type"],
            }],
        },
        headers={"Idempotency-Key": "source-mismatch"},
    )
    assert source_mismatch.status_code == 409
    assert source_mismatch.json()["detail"]["code"] == "source_hash_mismatch"

    unsafe = _create_payload()
    unsafe["fields"][0]["definition"] = {"default": "privileged client value"}
    rejected = await client.post(
        "/api/template-studio/drafts", json=unsafe,
        headers={"Idempotency-Key": "unsafe-durable-payload"},
    )
    assert rejected.status_code == 422
    assert rejected.json()["detail"]["code"] == "unsafe_durable_payload"

    too_many = _create_payload()
    too_many["fields"] = [too_many["fields"][0]] * 201
    too_many["placements"] = []
    bounded = await client.post(
        "/api/template-studio/drafts", json=too_many,
        headers={"Idempotency-Key": "field-count-bound"},
    )
    assert bounded.status_code == 422


async def test_snapshot_hash_immutability_archive_cancel_and_evidence_recheck(
    client, db_session, test_tenant, test_user
):
    created = await client.post(
        "/api/template-studio/drafts", json=_create_payload(),
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
    canonical = __import__("json").dumps(
        snapshot["payload"], sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    assert hashlib.sha256(canonical).hexdigest() == snapshot["content_sha256"]
    durable = str(snapshot["payload"]).lower()
    for forbidden in ("privileged client value", "storage_path", "signed_url", "provider_id"):
        assert forbidden not in durable

    row = await db_session.get(StudioDraftSnapshot, uuid.UUID(snapshot["id"]))
    original_hash = row.content_sha256
    snapshot["payload"]["title"] = "client-side mutation"
    await db_session.refresh(row)
    assert row.content_sha256 == original_hash

    service = StudioDraftService(db_session, test_tenant.id, test_user.id)
    assert await service.mark_render_evidence_if_current(
        uuid.UUID(draft["id"]), 1, draft["identity_sha256"]
    ) is True

    archived = await client.patch(
        f"/api/template-studio/drafts/{draft['id']}",
        json={"base_revision": 1, "operations": [{"op": "archive"}, {"op": "request_cancel"}]},
        headers={"Idempotency-Key": "archive-and-cancel"},
    )
    assert archived.status_code == 200, archived.text
    result = archived.json()
    assert result["revision"] == 2
    assert result["lifecycle_state"] == "archived"
    assert result["cancellation_requested"] is True
    assert result["evidence_invalidated"] is True
    assert await service.mark_render_evidence_if_current(
        uuid.UUID(draft["id"]), 1, draft["identity_sha256"]
    ) is False


async def test_published_template_import_and_safe_compatibility_promote(
    client, db_session, test_tenant
):
    body = "Dear {{client_name}}"
    template = DocumentTemplate(
        id=uuid.uuid4(), tenant_id=test_tenant.id, title="Existing",
        body=body, format="markdown", status="draft",
        source_sha256=hashlib.sha256(body.encode()).hexdigest(),
        source_content_type="text/markdown",
        variable_schema={"version": 1, "fields": [{
            "name": "client_name", "label": "Client", "type": "text", "required": True
        }]},
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
        json={"expected_revision": 1, "status": "active"},
        headers={"Idempotency-Key": "promote-compatibility"},
    )
    assert promoted.status_code == 200, promoted.text
    await db_session.refresh(template)
    assert template.status == "active"
    assert template.variable_schema["version"] == 2
    assert template.variable_schema["fields"][0]["studio_field_id"] == field_id
    assert template.body == body


async def test_proposal_acceptance_seam_advances_exactly_one_revision(
    db_session, test_tenant, test_user
):
    service = StudioDraftService(db_session, test_tenant.id, test_user.id)
    # The Phase 4 seam uses the same bounded patch transaction with a distinct
    # operation name. The endpoint is intentionally not exposed in Phase 2.
    from app.schemas.studio_draft import StudioDraftCreate

    draft = await service.create(
        StudioDraftCreate.model_validate(_create_payload()), "proposal-seam-create"
    )
    patched = await service.patch(
        uuid.UUID(str(draft["id"])),
        StudioDraftPatch.model_validate({
            "base_revision": 1,
            "operations": [{"op": "set_metadata", "title": "Accepted proposal"}],
        }),
        "proposal-seam-accept",
        operation="accept_proposal",
    )
    assert patched["revision"] == 2
    row = await db_session.get(StudioDraft, uuid.UUID(str(draft["id"])))
    assert row.revision == 2
