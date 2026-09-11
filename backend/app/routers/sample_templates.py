"""Global sample-template library router.

Read-only catalog of platform-owned starter templates available to every
authenticated tenant. Unlike ``/api/templates`` (tenant-owned, RLS-scoped),
this catalog is shared content: tenants may list, inspect, download, and render
samples, but cannot mutate them. Seeding is done by
``scripts/seed_sample_templates.py``.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.middleware.tenant import get_current_user
from app.models.sample_template import SampleTemplate
from app.schemas.sample_template import (
    SampleTemplateListResponse,
    SampleTemplateRenderRequest,
    SampleTemplateResponse,
)
from app.services.access_control import require_capability
from app.services.pdf_templates import TemplatePdfError, fill_pdf_template

router = APIRouter(prefix="/api/templates/library", tags=["sample-templates"])
settings = get_settings()


def _seed_root() -> Path:
    if settings.SAMPLE_TEMPLATE_DIR:
        return Path(settings.SAMPLE_TEMPLATE_DIR).resolve()
    return (Path(__file__).resolve().parents[2] / "seed" / "sample_templates").resolve()


def _safe_generated_filename(title: str, extension: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._ -]+", "_", (title or "sample"))
    stem = stem.replace("..", ".").strip(" .")[:180] or "sample"
    return f"{stem}.{extension.lstrip('.')}"


async def _load_sample(db: AsyncSession, sample_id: uuid.UUID) -> SampleTemplate:
    sample = await db.scalar(
        select(SampleTemplate).where(
            SampleTemplate.id == sample_id,
            SampleTemplate.is_active.is_(True),
        )
    )
    if not sample:
        raise HTTPException(status_code=404, detail="Sample template not found")
    return sample


def _verified_source(sample: SampleTemplate) -> bytes:
    root = _seed_root()
    target = (root / sample.source_filename).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise HTTPException(
            status_code=409, detail="The sample template source is unavailable"
        )
    content = target.read_bytes()
    if hashlib.sha256(content).hexdigest() != sample.source_sha256:
        raise HTTPException(
            status_code=409, detail="The sample template failed its integrity check"
        )
    return content


@router.get("", response_model=SampleTemplateListResponse)
async def list_sample_templates(
    category: str | None = Query(None),
    jurisdiction: str | None = Query(None),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    filters = [SampleTemplate.is_active.is_(True)]
    if category:
        filters.append(SampleTemplate.category == category)

    result = await db.execute(select(SampleTemplate).where(*filters))
    samples = result.scalars().all()

    if jurisdiction:
        samples = [
            sample for sample in samples if jurisdiction in (sample.jurisdictions or [])
        ]

    count_stmt = select(func.count()).select_from(
        select(SampleTemplate).where(*filters).subquery()
    )
    total = (await db.execute(count_stmt)).scalar_one()

    return SampleTemplateListResponse(
        items=[SampleTemplateResponse.model_validate(s) for s in samples],
        total=total,
    )


@router.get("/{sample_id}", response_model=SampleTemplateResponse)
async def get_sample_template(
    sample_id: uuid.UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    sample = await _load_sample(db, sample_id)
    return SampleTemplateResponse.model_validate(sample)


@router.get("/{sample_id}/source")
async def download_sample_source(
    sample_id: uuid.UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    sample = await _load_sample(db, sample_id)
    content = await asyncio.to_thread(_verified_source, sample)
    filename = _safe_generated_filename(sample.title, "pdf")
    return Response(
        content=content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
            "Pragma": "no-cache",
        },
    )


@router.post("/{sample_id}/render-file")
async def render_sample_template(
    sample_id: uuid.UUID,
    payload: SampleTemplateRenderRequest,
    current_user=Depends(require_capability("manage_documents")),
    db: AsyncSession = Depends(get_db),
):
    sample = await _load_sample(db, sample_id)
    if not sample.variable_schema:
        raise HTTPException(
            status_code=409,
            detail="The sample template has not been seeded with a field schema.",
        )
    content = await asyncio.to_thread(_verified_source, sample)
    try:
        output = await asyncio.to_thread(
            fill_pdf_template,
            content,
            variable_schema=sample.variable_schema,
            variables=payload.variables,
            flatten=True,
            enforce_required=False,
        )
    except TemplatePdfError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    filename = _safe_generated_filename(sample.title, "pdf")
    return Response(
        content=output,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
            "Pragma": "no-cache",
        },
    )
