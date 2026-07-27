"""Feature-gated Microsoft Office action planning endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.schemas.office_assistant import (
    OfficeActionPlan,
    OfficeActionResult,
    OfficePlanRequest,
    OfficePolicyResponse,
    OfficeResultAcknowledgement,
)
from app.services.office_access import (
    require_office_globally_enabled,
    require_office_pilot_tenant,
)
from app.services.office_action_policy import ALLOWED_ACTIONS, OfficePolicyError
from app.services.office_assistant import (
    OfficeGenerationError,
    office_assistant_service,
)

settings = get_settings()
router = APIRouter(prefix="/api/office", tags=["office-assistant"])


def _require_office_enabled() -> None:
    require_office_globally_enabled()


def _policy_error(exc: OfficePolicyError) -> HTTPException:
    if exc.code == "plan_not_found":
        status = 404
    elif exc.code == "plan_already_decided":
        status = 409
    else:
        status = 422
    return HTTPException(
        status_code=status,
        detail={"code": exc.code, "message": str(exc)},
    )


@router.get("/policy", response_model=OfficePolicyResponse)
async def get_office_policy(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _require_office_enabled()
    user = await get_current_user(request, db)
    require_office_pilot_tenant(user.tenant_id)
    await set_tenant_context(db, str(user.tenant_id))
    return OfficePolicyResponse(
        enabled=True,
        plan_ttl_seconds=settings.OFFICE_PLAN_TTL_SECONDS,
        max_word_characters=settings.OFFICE_MAX_WORD_CHARACTERS,
        max_excel_cells=settings.OFFICE_MAX_EXCEL_CELLS,
        max_outlook_characters=settings.OFFICE_MAX_OUTLOOK_CHARACTERS,
        allowed_actions=ALLOWED_ACTIONS,
    )


@router.post("/plans", response_model=OfficeActionPlan, status_code=201)
async def create_office_plan(
    body: OfficePlanRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _require_office_enabled()
    user = await get_current_user(request, db)
    require_office_pilot_tenant(user.tenant_id)
    await set_tenant_context(db, str(user.tenant_id))
    try:
        return await office_assistant_service.create_plan(db, user, body)
    except OfficePolicyError as exc:
        raise _policy_error(exc) from exc
    except OfficeGenerationError as exc:
        raise HTTPException(
            status_code=502,
            detail={"code": "invalid_model_plan", "message": str(exc)},
        ) from exc


@router.post(
    "/plans/{plan_id}/result",
    response_model=OfficeResultAcknowledgement,
)
async def record_office_result(
    plan_id: str,
    body: OfficeActionResult,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _require_office_enabled()
    if body.plan_id != plan_id:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "plan_id_mismatch",
                "message": "Path and body plan IDs must match",
            },
        )
    user = await get_current_user(request, db)
    require_office_pilot_tenant(user.tenant_id)
    await set_tenant_context(db, str(user.tenant_id))
    try:
        run = await office_assistant_service.record_result(db, user, body)
    except OfficePolicyError as exc:
        raise _policy_error(exc) from exc
    return OfficeResultAcknowledgement(plan_id=run.plan_id, status=run.status)
