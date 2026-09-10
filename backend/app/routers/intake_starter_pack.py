"""Standard matter-initiation paperwork: the pack a firm starts every client with."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, set_tenant_context
from app.models.plugin import Matter
from app.services import intake_starter_pack as service
from app.services.access_control import require_capability
from app.services.matter_access import can_access_matter

router = APIRouter(prefix="/api/intake-starter-pack", tags=["matter-intake"])


async def _matter_labels(
    db: AsyncSession, user, matter_id: uuid.UUID
) -> tuple[str, str]:
    """Return the matter's type and practice area, once the caller may read it."""

    if not await can_access_matter(
        db,
        tenant_id=user.tenant_id,
        user_id=user.id,
        is_admin=user.role == "admin",
        matter_id=matter_id,
    ):
        raise HTTPException(404, "Matter not found")
    matter = await db.scalar(
        select(Matter).where(Matter.id == matter_id, Matter.tenant_id == user.tenant_id)
    )
    if matter is None:
        raise HTTPException(404, "Matter not found")
    return matter.matter_type or "", matter.practice_area or ""


@router.get("")
async def read_pack(
    matter_type: str = Query("", max_length=200),
    practice_area: str = Query("", max_length=200),
    matter_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_capability("manage_matters")),
):
    """Return the questionnaire, requested uploads, and documents for a matter.

    ``matter_id`` reads the labels off an existing matter; ``matter_type`` and
    ``practice_area`` cover the new-matter form, where there is no matter yet.
    """

    await set_tenant_context(db, str(user.tenant_id))
    if matter_id is not None:
        matter_type, practice_area = await _matter_labels(db, user, matter_id)
    return service.pack(matter_type, practice_area)


@router.get("/practices")
async def list_practices(
    user=Depends(require_capability("manage_matters")),
):
    """Return every practice pack, so staff can see which types are recognised."""

    return [
        {
            "practice": practice.slug,
            "label": practice.label,
            "matter_types": list(practice.aliases),
            "question_count": len(service.CORE_QUESTIONS) + len(practice.questions),
        }
        for practice in service.practices()
    ]


@router.post("/documents", status_code=201)
async def install_documents(
    db: AsyncSession = Depends(get_db),
    user=Depends(require_capability("manage_documents")),
):
    """Add the fee agreement and client intake form to the tenant's library.

    They arrive as unapproved drafts. An attorney reviews them for the firm's
    jurisdiction and approves them through the normal template path before any
    client receives one; an existing template of the same name is left alone.
    """

    await set_tenant_context(db, str(user.tenant_id))
    return {"documents": await service.install(db, user.tenant_id)}
