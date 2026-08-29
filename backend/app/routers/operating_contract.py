"""Public, sanitized operating-contract endpoint."""

from fastapi import APIRouter

from app.services.operating_contract import operating_contract

router = APIRouter(prefix="/api/public", tags=["public-trust"])


@router.get("/operating-contract")
async def get_operating_contract():
    return operating_contract()
