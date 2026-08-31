"""Focused contract tests for the Template Studio draft foundation."""

import asyncio
import hashlib
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from io import BytesIO

import pytest
from docx import Document
from pypdf import PdfWriter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import get_settings
from app.models.document_template import DocumentTemplate
from app.models.rbac import Role, UserRole
from app.models.studio_draft import (
    StudioDraft,
    StudioDraftPlacement,
    StudioDraftSnapshot,
    StudioSourceArtifact,
)
from app.schemas.studio_draft import (
    StudioDraftCreate,
    StudioDraftPatch,
    StudioRevisionRequest,
)
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


def _docx_bytes(text="Studio source"):
    output = BytesIO()
    document = Document()
    document.add_paragraph(text)
    document.save(output)
    return output.getvalue()


def _pdf_bytes():
    output = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.write(output)
    return output.getvalue()


def _tracked_change_docx_bytes():
    source = BytesIO(_docx_bytes())
    output = BytesIO()
    with (
        zipfile.ZipFile(source) as archive,
        zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as rewritten,
    ):
        for item in archive.infolist():
            content = archive.read(item.filename)
            if item.filename == "word/document.xml":
                content = content.replace(
                    b"<w:body>",
                    b'<w:body><w:ins w:id="1" w:author="Editor"><w:r><w:t>change</w:t></w:r></w:ins>',
                    1,
                )
            rewritten.writestr(item, content)
    return output.getvalue()


def _mutated_docx_bytes(*, relationship=None, document_fragment=None, extra_part=None):
    source = BytesIO(_docx_bytes())
    output = BytesIO()
    with (
        zipfile.ZipFile(source) as archive,
        zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as rewritten,
    ):
        for item in archive.infolist():
            content = archive.read(item.filename)
            if relationship and item.filename == "word/_rels/document.xml.rels":
                content = content.replace(
                    b"</Relationships>", relationship + b"</Relationships>", 1
                )
            if document_fragment and item.filename == "word/document.xml":
                content = content.replace(
                    b"<w:body>", b"<w:body>" + document_fragment, 1
                )
            rewritten.writestr(item, content)
        if extra_part:
            rewritten.writestr(extra_part, b"unsafe payload")
    return output.getvalue()


async def _register_source(client, content=None, format_name="docx", media_type=None):
    content = content if content is not None else _docx_bytes()
    media_type = (
        media_type
        or {
            "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "pdf": "application/pdf",
            "markdown": "text/markdown",
        }[format_name]
    )
    response = await client.post(
        "/api/template-studio/drafts/sources",
        data={"format": format_name},
        files={"source": ("template.bin", content, media_type)},
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert set(payload) == {
        "contract_version",
        "artifact_id",
        "sha256",
        "media_type",
        "format",
    }
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
    registered = await service.register_source(
        b"trusted bytes", "markdown", "text/markdown"
    )
    replay = await service.register_source(
        b"trusted bytes", "markdown", "text/markdown"
    )
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


async def test_source_registration_rejects_spoofed_and_hostile_content(client):
    spoofed = await client.post(
        "/api/template-studio/drafts/sources",
        data={"format": "docx"},
        files={"source": ("source.docx", _docx_bytes(), "application/pdf")},
    )
    assert spoofed.status_code == 422
    assert spoofed.json()["detail"]["code"] == "source_format_media_mismatch"

    malformed_pdf = await client.post(
        "/api/template-studio/drafts/sources",
        data={"format": "pdf"},
        files={"source": ("source.pdf", b"not a PDF", "application/pdf")},
    )
    assert malformed_pdf.status_code == 422
    assert malformed_pdf.json()["detail"]["code"] == "invalid_source_content"

    tracked = await client.post(
        "/api/template-studio/drafts/sources",
        data={"format": "docx"},
        files={
            "source": (
                "source.docx",
                _tracked_change_docx_bytes(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    assert tracked.status_code == 422
    assert tracked.json()["detail"]["code"] == "invalid_source_content"

    invalid_text = await client.post(
        "/api/template-studio/drafts/sources",
        data={"format": "markdown"},
        files={"source": ("source.md", b"hello\x00secret", "text/plain")},
    )
    assert invalid_text.status_code == 422
    assert invalid_text.json()["detail"]["code"] == "invalid_source_content"


@pytest.mark.parametrize(
    "content",
    [
        _mutated_docx_bytes(
            relationship=(
                b'<Relationship Id="rId900" '
                b'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/attachedTemplate" '
                b'Target="https://attacker.invalid/template.dotm" TargetMode="External"/>'
            )
        ),
        _mutated_docx_bytes(
            relationship=(
                b'<Relationship Id="rId901" '
                b'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
                b'Target="https://attacker.invalid/pixel.png" TargetMode="External"/>'
            )
        ),
        _mutated_docx_bytes(document_fragment=b'<w:altChunk r:id="rId902"/>'),
        _mutated_docx_bytes(extra_part="word/vbaProject.bin"),
        _mutated_docx_bytes(extra_part="word/embeddings/payload.bin"),
    ],
    ids=[
        "attached-template",
        "external-image",
        "altchunk",
        "macro",
        "embedded-payload",
    ],
)
async def test_source_registration_rejects_active_external_docx_packages(
    client, content
):
    response = await client.post(
        "/api/template-studio/drafts/sources",
        data={"format": "docx"},
        files={
            "source": (
                "source.docx",
                content,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_source_content"


async def test_source_registration_permits_safe_external_docx_hyperlink(client):
    content = _mutated_docx_bytes(
        relationship=(
            b'<Relationship Id="rId903" '
            b'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
            b'Target="https://example.invalid/safe" TargetMode="External"/>'
        )
    )
    source = await _register_source(client, content, "docx")
    assert source["format"] == "docx"


async def test_source_format_is_enforced_on_create_and_replace(client):
    markdown = await _register_source(
        client, b"Hello {{client_name}}", "markdown", "text/plain"
    )
    wrong_create = await client.post(
        "/api/template-studio/drafts",
        json=_create_payload(markdown["artifact_id"]),
        headers={"Idempotency-Key": "wrong-format-create"},
    )
    assert wrong_create.status_code == 422
    assert wrong_create.json()["detail"]["code"] == "source_format_mismatch"

    docx = await _register_source(client)
    created = await client.post(
        "/api/template-studio/drafts",
        json=_create_payload(docx["artifact_id"]),
        headers={"Idempotency-Key": "right-format-create"},
    )
    assert created.status_code == 201, created.text
    wrong_replace = await client.patch(
        f"/api/template-studio/drafts/{created.json()['id']}",
        json={
            "base_revision": 1,
            "operations": [
                {
                    "op": "replace_source",
                    "source_artifact_id": markdown["artifact_id"],
                }
            ],
        },
        headers={"Idempotency-Key": "wrong-format-replace"},
    )
    assert wrong_replace.status_code == 422
    assert wrong_replace.json()["detail"]["code"] == "source_format_mismatch"


async def test_source_quota_admission_is_atomic_and_dedupes_before_charge(
    db_session, test_engine, test_tenant, test_user, monkeypatch
):
    settings = get_settings()
    monkeypatch.setattr(settings, "TEMPLATE_STUDIO_SOURCE_ARTIFACT_QUOTA", 1)
    monkeypatch.setattr(settings, "TEMPLATE_STUDIO_SOURCE_BYTES_QUOTA", 1024 * 1024)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async def register(content):
        async with factory() as session:
            service = StudioDraftService(session, test_tenant.id, test_user.id)
            try:
                return await service.register_source(
                    content, "markdown", "text/markdown"
                )
            except StudioError as exc:
                await session.rollback()
                return exc

    identical = await asyncio.gather(
        register(b"same tenant source"), register(b"same tenant source")
    )
    assert identical[0]["artifact_id"] == identical[1]["artifact_id"]

    rejected = await register(b"distinct tenant source")
    assert isinstance(rejected, StudioError)
    assert rejected.status_code == 429
    assert rejected.detail["code"] == "source_artifact_quota_exceeded"

    rows = list(
        (
            await db_session.scalars(
                select(StudioSourceArtifact).where(
                    StudioSourceArtifact.tenant_id == test_tenant.id
                )
            )
        ).all()
    )
    assert len(rows) == 1


async def test_source_aggregate_byte_quota_has_stable_rejection(
    db_session, test_tenant, test_user, monkeypatch
):
    service = StudioDraftService(db_session, test_tenant.id, test_user.id)
    monkeypatch.setattr(service.settings, "TEMPLATE_STUDIO_SOURCE_ARTIFACT_QUOTA", 10)
    monkeypatch.setattr(service.settings, "TEMPLATE_STUDIO_SOURCE_BYTES_QUOTA", 20)
    await service.register_source(b"1234567890", "markdown", "text/markdown")
    with pytest.raises(StudioError) as caught:
        await service.register_source(b"abcdefghijk", "markdown", "text/markdown")
    assert caught.value.status_code == 429
    assert caught.value.detail["code"] == "source_bytes_quota_exceeded"
    await db_session.rollback()


async def test_orphan_cleanup_is_bounded_and_never_deletes_referenced_source(
    db_session, test_tenant, test_user, monkeypatch
):
    old_time = datetime.now(timezone.utc) - timedelta(hours=3)

    def old_source(content: bytes) -> StudioSourceArtifact:
        return StudioSourceArtifact(
            tenant_id=test_tenant.id,
            sha256=hashlib.sha256(content).hexdigest(),
            media_type="text/markdown",
            format="markdown",
            byte_size=len(content),
            resolver_key=f"studio-db:v1:{uuid.uuid4()}",
            content_bytes=content,
            created_by_user_id=test_user.id,
            created_at=old_time,
        )

    referenced = old_source(b"Referenced {{name}}")
    orphan = old_source(b"Unreferenced source")
    db_session.add_all([referenced, orphan])
    await db_session.commit()

    service = StudioDraftService(db_session, test_tenant.id, test_user.id)
    monkeypatch.setattr(service.settings, "TEMPLATE_STUDIO_SOURCE_ORPHAN_TTL_HOURS", 1)
    draft = await service.create(
        StudioDraftCreate.model_validate(
            {
                "title": "Referenced markdown",
                "format": "markdown",
                "source_artifact_id": str(referenced.id),
                "fields": [],
                "placements": [],
            }
        ),
        "orphan-cleanup-reference",
    )
    snapshot = await service.snapshot(
        uuid.UUID(str(draft["id"])),
        StudioRevisionRequest(expected_revision=1),
        "orphan-cleanup-snapshot",
    )
    replacement = await service.register_source(
        b"Replacement {{name}}", "markdown", "text/markdown"
    )
    await service.patch(
        uuid.UUID(str(draft["id"])),
        StudioDraftPatch.model_validate(
            {
                "base_revision": 1,
                "operations": [
                    {
                        "op": "replace_source",
                        "source_artifact_id": replacement["artifact_id"],
                    }
                ],
            }
        ),
        "orphan-cleanup-replace",
    )

    assert await service.purge_expired_source_orphans(limit=1) == 1
    assert await db_session.get(StudioSourceArtifact, orphan.id) is None
    assert await db_session.get(StudioSourceArtifact, referenced.id) is not None
    snapshot_row = await db_session.get(
        StudioDraftSnapshot, uuid.UUID(str(snapshot["id"]))
    )
    assert snapshot_row.source_artifact_id == referenced.id
    assert await service.purge_expired_source_orphans(limit=1) == 0


async def test_cleanup_vs_create_serializes_source_attachment(
    db_session, test_engine, test_tenant, test_user, monkeypatch
):
    content = b"Old create-race source"
    artifact = StudioSourceArtifact(
        tenant_id=test_tenant.id,
        sha256=hashlib.sha256(content).hexdigest(),
        media_type="text/markdown",
        format="markdown",
        byte_size=len(content),
        resolver_key=f"studio-db:v1:{uuid.uuid4()}",
        content_bytes=content,
        created_by_user_id=test_user.id,
        created_at=datetime.now(timezone.utc) - timedelta(hours=3),
    )
    db_session.add(artifact)
    await db_session.commit()
    monkeypatch.setattr(get_settings(), "TEMPLATE_STUDIO_SOURCE_ORPHAN_TTL_HOURS", 1)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async def cleanup():
        async with factory() as session:
            return await StudioDraftService(
                session, test_tenant.id, test_user.id
            ).purge_expired_source_orphans(limit=10)

    async def create():
        async with factory() as session:
            service = StudioDraftService(session, test_tenant.id, test_user.id)
            try:
                return await service.create(
                    StudioDraftCreate.model_validate(
                        {
                            "title": "Create race",
                            "format": "markdown",
                            "source_artifact_id": artifact.id,
                            "fields": [],
                            "placements": [],
                        }
                    ),
                    "cleanup-create-race",
                )
            except StudioError as exc:
                await session.rollback()
                return exc

    deleted, attached = await asyncio.gather(cleanup(), create())
    if isinstance(attached, StudioError):
        assert attached.status_code == 404
        assert deleted == 1
    else:
        assert deleted == 0
        assert attached["source"]["artifact_id"] == artifact.id


async def test_cleanup_vs_replace_serializes_source_attachment(
    db_session, test_engine, test_tenant, test_user, monkeypatch
):
    bootstrap = StudioDraftService(db_session, test_tenant.id, test_user.id)
    current = await bootstrap.register_source(
        b"Current replace-race source", "markdown", "text/markdown"
    )
    draft = await bootstrap.create(
        StudioDraftCreate.model_validate(
            {
                "title": "Replace race",
                "format": "markdown",
                "source_artifact_id": current["artifact_id"],
                "fields": [],
                "placements": [],
            }
        ),
        "cleanup-replace-create",
    )
    target_content = b"Old replace-race target"
    target = StudioSourceArtifact(
        tenant_id=test_tenant.id,
        sha256=hashlib.sha256(target_content).hexdigest(),
        media_type="text/markdown",
        format="markdown",
        byte_size=len(target_content),
        resolver_key=f"studio-db:v1:{uuid.uuid4()}",
        content_bytes=target_content,
        created_by_user_id=test_user.id,
        created_at=datetime.now(timezone.utc) - timedelta(hours=3),
    )
    db_session.add(target)
    await db_session.commit()
    monkeypatch.setattr(get_settings(), "TEMPLATE_STUDIO_SOURCE_ORPHAN_TTL_HOURS", 1)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async def cleanup():
        async with factory() as session:
            return await StudioDraftService(
                session, test_tenant.id, test_user.id
            ).purge_expired_source_orphans(limit=10)

    async def replace():
        async with factory() as session:
            service = StudioDraftService(session, test_tenant.id, test_user.id)
            try:
                return await service.patch(
                    uuid.UUID(str(draft["id"])),
                    StudioDraftPatch.model_validate(
                        {
                            "base_revision": 1,
                            "operations": [
                                {
                                    "op": "replace_source",
                                    "source_artifact_id": target.id,
                                }
                            ],
                        }
                    ),
                    "cleanup-replace-race",
                )
            except StudioError as exc:
                await session.rollback()
                return exc

    deleted, replaced = await asyncio.gather(cleanup(), replace())
    if isinstance(replaced, StudioError):
        assert replaced.status_code == 404
        assert deleted == 1
    else:
        assert deleted == 0
        assert replaced["source"]["artifact_id"] == target.id


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


async def test_compatibility_import_infers_pdf_contract_and_rejects_bad_bytes(
    client, db_session, test_tenant, tmp_path, monkeypatch
):
    monkeypatch.setattr(get_settings(), "UPLOAD_DIR", str(tmp_path))
    source = _pdf_bytes()
    template = DocumentTemplate(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        title="Existing PDF",
        body="",
        format="pdf",
        status="draft",
        source_sha256=hashlib.sha256(source).hexdigest(),
        source_content_type="text/plain",
        source_file_size=len(source),
        variable_schema={"fields": []},
    )
    source_dir = tmp_path / str(test_tenant.id) / "templates" / str(template.id)
    source_dir.mkdir(parents=True)
    source_path = source_dir / "source.pdf"
    source_path.write_bytes(source)
    template.source_storage_path = str(source_path)
    db_session.add(template)
    await db_session.commit()

    imported = await client.post(
        "/api/template-studio/drafts/imports",
        json={"template_id": str(template.id)},
        headers={"Idempotency-Key": "import-pdf-canonical-media"},
    )
    assert imported.status_code == 201, imported.text
    contract = imported.json()["source"]
    assert contract["format"] == "pdf"
    assert contract["media_type"] == "application/pdf"

    malformed = b"%PDF-malformed"
    bad_template = DocumentTemplate(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        title="Bad PDF",
        body="",
        format="pdf",
        status="draft",
        source_sha256=hashlib.sha256(malformed).hexdigest(),
        source_content_type="application/pdf",
        source_file_size=len(malformed),
        variable_schema={"fields": []},
    )
    bad_dir = tmp_path / str(test_tenant.id) / "templates" / str(bad_template.id)
    bad_dir.mkdir(parents=True)
    bad_path = bad_dir / "source.pdf"
    bad_path.write_bytes(malformed)
    bad_template.source_storage_path = str(bad_path)
    db_session.add(bad_template)
    await db_session.commit()

    rejected = await client.post(
        "/api/template-studio/drafts/imports",
        json={"template_id": str(bad_template.id)},
        headers={"Idempotency-Key": "import-pdf-malformed"},
    )
    assert rejected.status_code == 422
    assert rejected.json()["detail"]["code"] == "invalid_source_content"


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
        _docx_bytes("quota source"),
        "docx",
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
        _docx_bytes("proposal seam"),
        "docx",
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
