"""Reviewed folder/ZIP imports for matter managers, independent of mail domains."""

from __future__ import annotations

import mimetypes
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Literal
from types import SimpleNamespace

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, set_tenant_context
from app.models.communication_log import CommunicationLog
from app.models.contact import Contact
from app.models.external_import import (
    ExternalImportRun,
    ExternalRecordLink,
    ExternalSystemConnection,
)
from app.models.matter_assignment import MatterAssignment
from app.models.matter_document import MatterDocument
from app.models.matter_document_folder import MatterDocumentFolder
from app.models.plugin import Matter, MatterEvent
from app.services.access_control import require_capability
from app.services.matter_access import can_access_matter
from app.services.matter_document_organization import (
    create_folder,
    storage_routing_for_folder,
)
from app.services.matter_file_store import MatterFileStore
from app.services.matter_import_manifest import (
    MAX_ARCHIVE_BYTES,
    MAX_FILE_BYTES,
    MAX_FILES,
    file_manifest,
    open_archive,
    parse_eml,
    safe_path,
)

router = APIRouter(prefix="/api/matter-imports", tags=["matter-imports"])
PROVIDER = "matter_folder_v1"


class ImportFile(BaseModel):
    path: str
    size: int = Field(ge=0, le=MAX_FILE_BYTES)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    group: str = Field(min_length=1, max_length=200)

    _safe_path = field_validator("path")(safe_path)


class ImportPlan(BaseModel):
    id: uuid.UUID
    files: list[ImportFile] = Field(min_length=1, max_length=MAX_FILES)

    @model_validator(mode="after")
    def unique_paths(self):
        if len({f.path.casefold() for f in self.files}) != len(self.files):
            raise ValueError("Each source path must be unique.")
        return self


class Mapping(BaseModel):
    group: str = Field(min_length=1, max_length=200)
    matter_id: uuid.UUID | None = None
    contact_id: uuid.UUID | None = None
    first_name: str = Field(default="", max_length=200)
    last_name: str = Field(default="", max_length=200)
    organization_name: str = Field(default="", max_length=500)
    matter_name: str = Field(default="", max_length=500)
    case_number: str = Field(default="", max_length=100)
    intake: Literal["existing", "review", "required"] = "review"
    exclude: bool = False

    @model_validator(mode="after")
    def new_matter_details(self):
        if not self.exclude and not self.matter_id:
            if not self.matter_name.strip():
                raise ValueError("A matter name is required.")
            if (
                not self.contact_id
                and not self.organization_name.strip()
                and not (self.first_name.strip() and self.last_name.strip())
            ):
                raise ValueError(
                    "Select a client or enter first and last name / organization."
                )
        return self


class Approval(BaseModel):
    mappings: list[Mapping] = Field(min_length=1, max_length=200)
    former_addresses: list[str] = Field(default_factory=list, max_length=100)
    confirm: Literal[True]


async def matter_for_user(db, user, matter_id):
    if not await can_access_matter(
        db,
        tenant_id=user.tenant_id,
        user_id=user.id,
        is_admin=user.role == "admin",
        matter_id=matter_id,
    ):
        raise HTTPException(404, "Matter not found")
    return await db.scalar(
        select(Matter).where(Matter.id == matter_id, Matter.tenant_id == user.tenant_id)
    )


async def get_run(db, user, run_id, *, lock=False):
    await set_tenant_context(db, str(user.tenant_id))
    stmt = select(ExternalImportRun).where(
        ExternalImportRun.id == run_id,
        ExternalImportRun.tenant_id == user.tenant_id,
        ExternalImportRun.provider == PROVIDER,
        ExternalImportRun.created_by_user_id == user.id,
    )
    run = await db.scalar(stmt.with_for_update() if lock else stmt)
    if run is None:
        raise HTTPException(404, "Import not found")
    return run


def response(run):
    return {"id": str(run.id), "status": run.status, **run.manifest}


@router.post("/zip-preview")
async def zip_preview(
    file: UploadFile = File(...), user=Depends(require_capability("manage_matters"))
):
    content = await file.read(MAX_ARCHIVE_BYTES + 1)
    try:
        with open_archive(content) as archive:
            return {
                "files": [
                    file_manifest(i.filename, archive.read(i))
                    for i in archive.infolist()
                    if not i.is_dir()
                ]
            }
    except (ValueError, zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("")
async def plan(
    body: ImportPlan,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_capability("manage_matters")),
):
    await set_tenant_context(db, str(user.tenant_id))
    # A transaction lock makes browser retries and two tabs converge on one run.
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"{user.tenant_id}:import:{body.id}"},
    )
    manifest = {"files": [f.model_dump() for f in body.files]}
    existing = await db.scalar(
        select(ExternalImportRun).where(ExternalImportRun.id == body.id)
    )
    if existing:
        if (
            existing.tenant_id != user.tenant_id
            or existing.created_by_user_id != user.id
            or existing.provider != PROVIDER
        ):
            raise HTTPException(409, "Import identifier unavailable")
        if existing.manifest["files"] != manifest["files"]:
            raise HTTPException(
                409, "Selected files differ from this import. Start a new import."
            )
        return response(existing)
    connection = ExternalSystemConnection(
        id=body.id,
        tenant_id=user.tenant_id,
        provider=PROVIDER,
        external_key=str(body.id),
        display_name="Matter folder upload",
        created_by_user_id=user.id,
    )
    db.add(connection)
    await db.flush()
    run = ExternalImportRun(
        id=body.id,
        tenant_id=user.tenant_id,
        connection_id=connection.id,
        provider=PROVIDER,
        created_by_user_id=user.id,
        status="review",
        manifest=manifest,
    )
    db.add(run)
    await db.commit()
    return response(run)


@router.get("/{run_id}")
async def status(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_capability("manage_matters")),
):
    return response(await get_run(db, user, run_id))


@router.post("/{run_id}/approve")
async def approve(
    run_id: uuid.UUID,
    body: Approval,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_capability("manage_matters")),
):
    run = await get_run(db, user, run_id, lock=True)
    approval = body.model_dump(mode="json")
    if run.status != "review":
        if run.manifest.get("approval") != approval:
            raise HTTPException(
                409, "Mappings already confirmed; start a new import to change them."
            )
        return response(run)
    groups = {f["group"] for f in run.manifest["files"]}
    if len(body.mappings) != len(groups) or {m.group for m in body.mappings} != groups:
        raise HTTPException(422, "Confirm exactly one mapping for every source group.")
    destinations = {}
    for mapping in body.mappings:
        if mapping.exclude:
            destinations[mapping.group] = None
            continue
        if mapping.matter_id:
            matter = await matter_for_user(db, user, mapping.matter_id)
        else:
            contact = None
            if mapping.contact_id:
                contact = await db.scalar(
                    select(Contact).where(
                        Contact.id == mapping.contact_id,
                        Contact.tenant_id == user.tenant_id,
                    )
                )
                if contact is None:
                    raise HTTPException(404, "Client not found")
            else:
                contact = Contact(
                    id=uuid.uuid4(),
                    tenant_id=user.tenant_id,
                    entity_type="organization"
                    if mapping.organization_name
                    else "person",
                    contact_type="client",
                    first_name=mapping.first_name.strip() or None,
                    last_name=mapping.last_name.strip() or None,
                    organization_name=mapping.organization_name.strip() or None,
                )
                db.add(contact)
                await db.flush()
            matter = Matter(
                id=uuid.uuid5(run.id, mapping.group),
                tenant_id=user.tenant_id,
                user_id=user.id,
                slug=f"import-{uuid.uuid5(run.id, mapping.group).hex}",
                matter_name=mapping.matter_name.strip(),
                matter_type="general",
                status="open",
                stage={
                    "existing": "Active",
                    "review": "Transfer / Review Required",
                    "required": "Intake / Awaiting Documents",
                }[mapping.intake],
                source="folder_import",
                client_contact_id=contact.id,
                case_number=mapping.case_number or None,
                retention_until=(
                    datetime.now(timezone.utc) + timedelta(days=365 * 7)
                ).date(),
            )
            db.add(matter)
            await db.flush()
            db.add(
                MatterAssignment(
                    tenant_id=user.tenant_id,
                    matter_id=matter.id,
                    user_id=user.id,
                    role="owner",
                    is_primary=True,
                )
            )
            db.add(
                MatterEvent(
                    tenant_id=user.tenant_id,
                    matter_id=matter.id,
                    event_type="intake",
                    title="Existing matter imported",
                    content=f"Import {run.id}; intake choice: {mapping.intake}. No messages sent.",
                    note_type="system",
                    created_by=user.id,
                )
            )
        destinations[mapping.group] = str(matter.id)
    run.manifest = {
        **run.manifest,
        "approval": approval,
        "destinations": destinations,
        "results": {},
    }
    run.status = "uploading"
    run.approved_at = datetime.now(timezone.utc)
    await db.commit()
    return response(run)


async def ingest(db, user, run_id, path, content):
    run = await get_run(db, user, run_id, lock=True)
    if run.status == "review":
        raise HTTPException(409, "Confirm mappings first.")
    entry = next((f for f in run.manifest["files"] if f["path"] == path), None)
    if entry is None or file_manifest(path, content) != {
        k: entry[k] for k in ("path", "size", "sha256")
    }:
        raise HTTPException(409, "File does not match the reviewed manifest.")
    result = run.manifest.get("results", {}).get(path)
    if result and result["status"] in ("imported", "duplicate", "excluded"):
        return result
    destination = run.manifest["destinations"][entry["group"]]
    if destination is None:
        result = {"status": "excluded"}
    else:
        matter = await matter_for_user(db, user, uuid.UUID(destination))
        key = f"{matter.id}:{entry['sha256']}"
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"{user.tenant_id}:import-file:{key}"},
        )
        link = await db.scalar(
            select(ExternalRecordLink).where(
                ExternalRecordLink.tenant_id == user.tenant_id,
                ExternalRecordLink.provider == PROVIDER,
                ExternalRecordLink.source_row_key == key,
                ExternalRecordLink.target_table == "matter_documents",
            )
        )
        if link:
            result = {
                "status": "duplicate",
                "document_id": str(link.target_record_id),
                "matter_id": destination,
            }
        else:
            email = (
                parse_eml(content, run.manifest["approval"]["former_addresses"])
                if path.lower().endswith(".eml")
                else None
            )
            parent = None
            for name in path.split("/")[:-1]:
                folder = await db.scalar(
                    select(MatterDocumentFolder).where(
                        MatterDocumentFolder.tenant_id == user.tenant_id,
                        MatterDocumentFolder.matter_id == matter.id,
                        MatterDocumentFolder.parent_id
                        == (parent.id if parent else None),
                        MatterDocumentFolder.name == name,
                    )
                )
                parent = folder or await create_folder(
                    db,
                    tenant_id=user.tenant_id,
                    matter_id=matter.id,
                    name=name,
                    parent_id=parent.id if parent else None,
                    created_by_user_id=user.id,
                )
            category, segments = storage_routing_for_folder(parent)
            filename = path.split("/")[-1]
            mime = (
                "message/rfc822"
                if email
                else mimetypes.guess_type(filename)[0] or "application/octet-stream"
            )
            stored = await MatterFileStore().store_matter_file_result(
                db=db,
                tenant_id=str(user.tenant_id),
                matter_slug=matter.slug,
                category=category or ("correspondence" if email else "general"),
                filename=f"{entry['sha256'][:16]}_{filename}",
                content=content,
                content_type=mime,
                matter_cloud_folder=matter.cloud_folder,
                folder_path=segments,
            )
            if not stored.succeeded:
                raise HTTPException(503, "File storage failed; retry this file.")
            doc = MatterDocument(
                id=uuid.uuid4(),
                tenant_id=user.tenant_id,
                matter_id=matter.id,
                uploaded_by_user_id=user.id,
                filename=filename,
                folder_id=parent.id if parent else None,
                content_type=mime,
                file_size=len(content),
                storage_path=stored.storage_path,
                storage_provider=stored.provider,
                storage_backend=stored.backend,
                provider_object_id=stored.provider_item_id,
                provider_drive_id=stored.drive_id,
                provider_parent_id=stored.parent_id,
                document_category="correspondence" if email else "other",
                description=f"Imported from {path}; batch {run.id}",
                portal_visible=False,
            )
            db.add(doc)
            await db.flush()
            metadata = {
                "path": path,
                "sha256": entry["sha256"],
                "imported_by": str(user.id),
            }
            if email:
                metadata.update(
                    {
                        k: v
                        for k, v in email.items()
                        if k not in ("body", "occurred_at", "participants")
                    }
                )
                db.add(
                    CommunicationLog(
                        tenant_id=user.tenant_id,
                        matter_id=matter.id,
                        created_by_user_id=user.id,
                        document_id=doc.id,
                        channel="email",
                        status="logged",
                        direction=email["direction"],
                        subject=email["subject"],
                        body=email["body"],
                        occurred_at=email["occurred_at"] or datetime.now(timezone.utc),
                        external_ref=f"historical:{key}",
                        thread_ref=(
                            email["references"].split()[0]
                            if email["references"]
                            else email["message_id"]
                        )[:500]
                        or None,
                        participants=email["participants"],
                        summary="Historical import"
                        + (
                            "; original date unavailable"
                            if not email["occurred_at"]
                            else ""
                        ),
                    )
                )
            db.add(
                ExternalRecordLink(
                    tenant_id=user.tenant_id,
                    provider=PROVIDER,
                    source_table="files",
                    source_row_key=key,
                    import_run_id=run.id,
                    target_table="matter_documents",
                    target_record_id=doc.id,
                    metadata_json=metadata,
                )
            )
            result = {
                "status": "imported",
                "document_id": str(doc.id),
                "matter_id": destination,
            }
    results = {**run.manifest.get("results", {}), path: result}
    run.manifest = {**run.manifest, "results": results}
    if len(results) == len(run.manifest["files"]) and all(
        r["status"] in ("imported", "duplicate", "excluded") for r in results.values()
    ):
        run.status = "complete"
    await db.commit()
    return result


async def attempt_file(db, user, run_id, path, content):
    # A failed storage transaction expires ORM instances, including the caller.
    user = SimpleNamespace(id=user.id, tenant_id=user.tenant_id, role=user.role)
    try:
        return await ingest(db, user, run_id, path, content)
    except Exception as exc:
        await db.rollback()
        if isinstance(exc, HTTPException) and exc.status_code != 503:
            raise
        run = await get_run(db, user, run_id, lock=True)
        if not any(f["path"] == path for f in run.manifest["files"]):
            raise HTTPException(422, "File not in manifest") from exc
        result = {
            "status": "failed",
            "error": "File could not be imported. Check its format, mapping and storage connection, then retry.",
        }
        run.manifest = {
            **run.manifest,
            "results": {**run.manifest.get("results", {}), path: result},
        }
        await db.commit()
        return result


@router.post("/{run_id}/file")
async def upload(
    run_id: uuid.UUID,
    path: str = Form(...),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_capability("manage_matters")),
):
    return await attempt_file(
        db, user, run_id, path, await file.read(MAX_FILE_BYTES + 1)
    )


@router.post("/{run_id}/zip")
async def upload_zip(
    run_id: uuid.UUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_capability("manage_matters")),
):
    user = SimpleNamespace(id=user.id, tenant_id=user.tenant_id, role=user.role)
    await get_run(db, user, run_id)
    content = await file.read(MAX_ARCHIVE_BYTES + 1)
    try:
        with open_archive(content) as archive:
            for entry in archive.infolist():
                if not entry.is_dir():
                    await attempt_file(
                        db, user, run_id, safe_path(entry.filename), archive.read(entry)
                    )
    except (ValueError, zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return response(await get_run(db, user, run_id))
