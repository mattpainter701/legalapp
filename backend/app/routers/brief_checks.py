"""Tenant and matter scoped Brief Check API."""

from __future__ import annotations

import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.models.brief_check import BriefCheck, BriefCheckAudit
from app.models.matter_document import MatterDocument
from app.models.plugin import Matter
from app.schemas.brief_check import (
    BriefCheckDecision,
    BriefCheckListResponse,
    BriefCheckResponse,
)
from app.services.brief_check import (
    MAX_BYTES,
    analyze_brief,
    report_markdown,
    sha256_bytes,
    table_of_authorities_markdown,
)
from app.services.document_export import markdown_to_docx_bytes
from app.services.matter_file_store import MatterFileReadError, MatterFileStore
from app.utils.text_processing import extract_text_from_docx, extract_text_from_pdf

router = APIRouter(prefix="/api/matters/{matter_id}/brief-checks", tags=["brief-check"])
file_store = MatterFileStore()


async def _matter(matter_id: uuid.UUID, user, db: AsyncSession) -> Matter:
    row = await db.execute(
        select(Matter).where(Matter.id == matter_id, Matter.tenant_id == user.tenant_id)
    )
    matter = row.scalar_one_or_none()
    if matter is None:
        raise HTTPException(status_code=404, detail="Matter not found")
    return matter


async def _text(filename: str, content_type: str | None, data: bytes) -> str:
    lower = filename.lower()
    if (
        lower.endswith(".docx")
        or content_type
        == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ):
        return extract_text_from_docx(data)[:1_500_000]
    if lower.endswith(".pdf") or content_type == "application/pdf":
        return extract_text_from_pdf(data, max_pages=300, max_chars=1_500_000)
    raise HTTPException(
        status_code=415, detail="Brief Check supports DOCX and PDF only"
    )


def _response(row: BriefCheck) -> dict:
    return {
        "id": row.id,
        "matter_id": row.matter_id,
        "input_filename": row.input_filename,
        "input_sha256": row.input_sha256,
        "input_size": row.input_size,
        "status": row.status,
        "result": row.result_json,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


@router.post("", response_model=BriefCheckResponse, status_code=201)
async def create_brief_check(
    matter_id: uuid.UUID,
    file: UploadFile | None = File(default=None),
    selected_document_id: uuid.UUID | None = Form(default=None),
    opposing_file: UploadFile | None = File(default=None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    await set_tenant_context(db, str(current_user.tenant_id))
    await _matter(matter_id, current_user, db)
    if file is None and selected_document_id is None:
        raise HTTPException(
            status_code=400,
            detail="Upload a DOCX/PDF or select an existing matter document",
        )
    if file is not None and selected_document_id is not None:
        raise HTTPException(
            status_code=400, detail="Choose upload or selected document, not both"
        )
    if selected_document_id:
        doc = (
            await db.execute(
                select(MatterDocument).where(
                    MatterDocument.id == selected_document_id,
                    MatterDocument.matter_id == matter_id,
                    MatterDocument.tenant_id == current_user.tenant_id,
                )
            )
        ).scalar_one_or_none()
        if doc is None:
            raise HTTPException(status_code=404, detail="Selected document not found")
        try:
            data = await file_store.read_matter_file_bytes(
                db=db,
                tenant_id=str(current_user.tenant_id),
                document=doc,
                max_bytes=MAX_BYTES,
            )
        except MatterFileReadError as exc:
            raise HTTPException(
                status_code=409, detail=f"Selected document could not be read: {exc}"
            ) from exc
        filename, content_type = doc.filename, doc.content_type
    else:
        data = await file.read()
        if len(data) > MAX_BYTES:
            raise HTTPException(
                status_code=413, detail="Brief exceeds the 15 MB processing limit"
            )
        filename, content_type = file.filename or "brief", file.content_type
    text = await _text(filename, content_type, data)
    opposing_text = None
    if opposing_file is not None:
        opposing_data = await opposing_file.read()
        if len(opposing_data) > MAX_BYTES:
            raise HTTPException(
                status_code=413,
                detail="Opposing brief exceeds the 15 MB processing limit",
            )
        opposing_text = await _text(
            opposing_file.filename or "opposing",
            opposing_file.content_type,
            opposing_data,
        )
    digest = sha256_bytes(data)
    existing = await db.execute(
        select(BriefCheck).where(
            BriefCheck.tenant_id == current_user.tenant_id,
            BriefCheck.matter_id == matter_id,
            BriefCheck.input_sha256 == digest,
        )
    )
    row = existing.scalar_one_or_none()
    if row is None:
        row = BriefCheck(
            tenant_id=current_user.tenant_id,
            matter_id=matter_id,
            created_by_user_id=current_user.id,
            input_filename=filename,
            input_sha256=digest,
            input_size=len(data),
            result_json=analyze_brief(text, opposing_text=opposing_text),
        )
        db.add(row)
        await db.flush()
        db.add(
            BriefCheckAudit(
                tenant_id=current_user.tenant_id,
                brief_check_id=row.id,
                actor_user_id=current_user.id,
                action="created",
            )
        )
        await db.commit()
        await db.refresh(row)
    return _response(row)


@router.get("", response_model=BriefCheckListResponse)
async def list_brief_checks(
    matter_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    await set_tenant_context(db, str(current_user.tenant_id))
    await _matter(matter_id, current_user, db)
    rows = (
        (
            await db.execute(
                select(BriefCheck)
                .where(
                    BriefCheck.tenant_id == current_user.tenant_id,
                    BriefCheck.matter_id == matter_id,
                )
                .order_by(BriefCheck.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return {"items": [_response(row) for row in rows]}


@router.post("/{check_id}/decisions", response_model=BriefCheckResponse)
async def decide_brief_check(
    matter_id: uuid.UUID,
    check_id: uuid.UUID,
    body: BriefCheckDecision,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    await set_tenant_context(db, str(current_user.tenant_id))
    await _matter(matter_id, current_user, db)
    row = (
        await db.execute(
            select(BriefCheck).where(
                BriefCheck.id == check_id,
                BriefCheck.tenant_id == current_user.tenant_id,
                BriefCheck.matter_id == matter_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Brief Check not found")
    result = dict(row.result_json)
    found = False
    for group in ("citations", "quotations", "omitted_authority_candidates"):
        for item in result.get(group, []):
            if item.get("id") == body.item_id:
                item["attorney_decision"] = body.decision
                item["attorney_note"] = body.note
                found = True
    if not found:
        raise HTTPException(status_code=404, detail="Review item not found")
    row.result_json = result
    row.status = (
        "reviewed" if body.decision in {"accepted", "rejected"} else "needs_review"
    )
    db.add(
        BriefCheckAudit(
            tenant_id=current_user.tenant_id,
            brief_check_id=row.id,
            actor_user_id=current_user.id,
            action="decision",
            item_id=body.item_id,
            decision=body.decision,
            note=body.note,
        )
    )
    await db.commit()
    await db.refresh(row)
    return _response(row)


@router.get("/{check_id}/export/{kind}")
async def export_brief_check(
    matter_id: uuid.UUID,
    check_id: uuid.UUID,
    kind: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    await set_tenant_context(db, str(current_user.tenant_id))
    await _matter(matter_id, current_user, db)
    row = (
        await db.execute(
            select(BriefCheck).where(
                BriefCheck.id == check_id,
                BriefCheck.tenant_id == current_user.tenant_id,
                BriefCheck.matter_id == matter_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Brief Check not found")
    if kind == "report":
        content, name = (
            report_markdown(row.result_json),
            "brief-check-review-report.docx",
        )
    elif kind in {"toa", "table-of-authorities"}:
        content, name = (
            table_of_authorities_markdown(row.result_json),
            "table-of-authorities-draft.docx",
        )
    else:
        raise HTTPException(status_code=404, detail="Export kind not found")
    return Response(
        markdown_to_docx_bytes(content, name),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}",
            "X-Content-Type-Options": "nosniff",
        },
    )
