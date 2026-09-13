"""One firm address, authenticated staff submission, explicit to-do review."""

import uuid
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user, require_admin
from app.models.inbound_email import InboundEmail, InboundEmailAlias
from app.models.plugin import Matter
from app.models.tenant import Tenant, TenantSettings
from app.routers.matters_correspondence import (
    _alias_response,
    _get_matter_or_404,
    settings,
)
from app.services.email_task_tags import EmailTaskSuggestion
from app.services.firm_email_intake import active_staff
from app.services.inbound_email import (
    alias_lookup_hash,
    file_inbound_email,
    generate_alias_local_part,
    remove_quarantined_message,
)
from app.services.token_vault import encrypt_token

router = APIRouter(prefix="/api/firm-email-intake", tags=["firm-email-intake"])


async def staff_context(request, db):
    user = await get_current_user(request, db)
    if (
        user.role in {"client", "portal"}
        or user.principal_type != "human"
        or not user.is_active
    ):
        raise HTTPException(403, "Firm staff access required")
    await set_tenant_context(db, str(user.tenant_id))
    return user


async def active_alias(db, tenant_id):
    return (
        await db.execute(
            select(InboundEmailAlias).where(
                InboundEmailAlias.tenant_id == tenant_id,
                InboundEmailAlias.kind == "firm",
                InboundEmailAlias.status == "active",
            )
        )
    ).scalar_one_or_none()


async def intake_timezone(db, tenant_id):
    row = (
        await db.execute(
            select(TenantSettings).where(TenantSettings.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    return (
        (row.custom_config or {}).get("firm_email_timezone") if row else None
    ) or "UTC"


@router.get("")
async def get_intake(request: Request, db: AsyncSession = Depends(get_db)):
    user = await staff_context(request, db)
    result = _alias_response(await active_alias(db, user.tenant_id)).model_dump()
    result["timezone"] = await intake_timezone(db, user.tenant_id)
    result["staff"] = [
        {"id": u.id, "name": u.full_name or u.email, "email": u.email}
        for u in await active_staff(db, user.tenant_id)
    ]
    result["pending_count"] = await db.scalar(
        select(func.count())
        .select_from(InboundEmail)
        .join(InboundEmailAlias, InboundEmail.alias_id == InboundEmailAlias.id)
        .where(
            InboundEmail.tenant_id == user.tenant_id,
            InboundEmailAlias.kind == "firm",
            InboundEmail.status == "pending",
        )
    )
    return result


class IntakeSettings(BaseModel):
    action: str = Field(pattern="^(enable|rotate|disable|settings)$")
    timezone: str = "UTC"


@router.post("")
async def configure_intake(
    body: IntakeSettings, request: Request, db: AsyncSession = Depends(get_db)
):
    admin = await require_admin(request, db)
    await set_tenant_context(db, str(admin.tenant_id))
    try:
        ZoneInfo(body.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(422, "Choose a valid IANA time zone")
    if body.action in {"enable", "rotate"} and not settings.INBOUND_EMAIL_ENABLED:
        raise HTTPException(503, "Email intake is not configured on this deployment")
    # Serialize enable/rotation without touching any other firm's address.
    await db.execute(
        select(Tenant.id).where(Tenant.id == admin.tenant_id).with_for_update()
    )
    row = await active_alias(db, admin.tenant_id)
    if row and body.action in {"disable", "rotate"}:
        row.status = "revoked"
        row.revoked_at = datetime.now(timezone.utc)
        await db.flush()
        row = None
    if row is None and body.action in {"enable", "rotate"}:
        local_part = "f-" + generate_alias_local_part()[2:]
        db.add(
            InboundEmailAlias(
                tenant_id=admin.tenant_id,
                matter_id=None,
                kind="firm",
                token_hash=alias_lookup_hash(local_part),
                encrypted_local_part=encrypt_token(local_part),
                created_by_user_id=admin.id,
                status="active",
            )
        )
    tenant_settings = (
        await db.execute(
            select(TenantSettings).where(TenantSettings.tenant_id == admin.tenant_id)
        )
    ).scalar_one_or_none()
    if tenant_settings is None:
        tenant_settings = TenantSettings(tenant_id=admin.tenant_id)
        db.add(tenant_settings)
    tenant_settings.custom_config = {
        **(tenant_settings.custom_config or {}),
        "firm_email_timezone": body.timezone,
    }
    await db.commit()
    return await get_intake(request, db)


@router.get("/queue")
async def queue(
    request: Request, offset: int = Query(0, ge=0), db: AsyncSession = Depends(get_db)
):
    user = await staff_context(request, db)
    rows = (
        (
            await db.execute(
                select(InboundEmail)
                .join(InboundEmailAlias, InboundEmail.alias_id == InboundEmailAlias.id)
                .where(
                    InboundEmail.tenant_id == user.tenant_id,
                    InboundEmailAlias.kind == "firm",
                    InboundEmail.status == "pending",
                )
                .order_by(InboundEmail.created_at, InboundEmail.id)
                .offset(offset)
                .limit(50)
            )
        )
        .scalars()
        .all()
    )
    matters = (
        (
            await db.execute(
                select(Matter)
                .where(Matter.tenant_id == user.tenant_id, Matter.is_closed.is_(False))
                .order_by(Matter.matter_name)
            )
        )
        .scalars()
        .all()
    )
    return {
        "items": [
            {
                "id": row.id,
                "subject": row.subject,
                "sender": row.envelope_sender,
                "body_preview": row.body_preview,
                "created_at": row.created_at,
                "suggestion": (row.authentication_results or {}).get("firm_intake", {}),
            }
            for row in rows
        ],
        "matters": [{"id": m.id, "title": m.matter_name} for m in matters],
    }


class ReviewTodo(BaseModel):
    matter_id: uuid.UUID
    assigned_to_user_id: uuid.UUID
    title: str = Field(min_length=1, max_length=300)
    due_date: date | None = None


async def pending_item(db, tenant_id, item_id):
    row = (
        await db.execute(
            select(InboundEmail)
            .join(InboundEmailAlias, InboundEmail.alias_id == InboundEmailAlias.id)
            .where(
                InboundEmail.id == item_id,
                InboundEmail.tenant_id == tenant_id,
                InboundEmailAlias.kind == "firm",
            )
            .with_for_update(of=InboundEmail)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "Intake email not found")
    if row.status != "pending":
        raise HTTPException(409, "Email has already been reviewed")
    return row


@router.post("/queue/{item_id}/accept")
async def accept(
    item_id: uuid.UUID,
    body: ReviewTodo,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await staff_context(request, db)
    row = await pending_item(db, user.tenant_id, item_id)
    matter = await _get_matter_or_404(str(body.matter_id), user.tenant_id, db)
    if matter.is_closed:
        raise HTTPException(422, "Choose an open matter")
    if body.assigned_to_user_id not in {
        u.id for u in await active_staff(db, user.tenant_id)
    }:
        raise HTTPException(422, "Choose an active member of your firm")
    if not body.title.strip():
        raise HTTPException(422, "A to-do title is required")
    row.matter_id = matter.id
    suggestion = EmailTaskSuggestion(
        tag="task",
        title=body.title.strip(),
        task_type="general",
        priority="medium",
        due_date=body.due_date,
    )
    result = await file_inbound_email(
        db,
        item=row,
        matter=matter,
        reviewed_by_user_id=user.id,
        task_suggestion=suggestion,
        assigned_to_user_id=body.assigned_to_user_id,
    )
    return {"task_id": result.task.id, "matter_id": matter.id}


@router.post("/queue/{item_id}/reject")
async def reject(
    item_id: uuid.UUID, request: Request, db: AsyncSession = Depends(get_db)
):
    user = await staff_context(request, db)
    row = await pending_item(db, user.tenant_id, item_id)
    remove_quarantined_message(row)
    row.status = "rejected"
    row.raw_storage_path = None
    row.reviewed_by_user_id = user.id
    row.reviewed_at = datetime.now(timezone.utc)
    await db.commit()
    return {"status": "rejected"}
