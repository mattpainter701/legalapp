"""Plan-related self-service endpoints (upsell lead capture)."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.models.plan_upgrade import PlanUpgradeRequest

router = APIRouter(prefix="/plan", tags=["plan"])


class UpgradeRequestBody(BaseModel):
    note: str | None = None
    target_plan: str | None = "full-platform"


@router.post("/upgrade-request", status_code=202)
async def request_upgrade(
    body: UpgradeRequestBody,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await set_tenant_context(db, str(current_user.tenant_id))
    db.add(
        PlanUpgradeRequest(
            tenant_id=current_user.tenant_id,
            requested_by_user_id=current_user.id,
            target_plan=body.target_plan,
            note=body.note,
        )
    )
    await db.commit()
    return {"status": "received"}
