"""Public, sanitized operating-contract and review-packet endpoints."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.services.operating_contract import (
    assurance_program,
    operating_contract,
    security_review_packet,
    service_objectives,
    subprocessor_registry,
    support_policy,
)

router = APIRouter(prefix="/api/public", tags=["public-trust"])


@router.get("/operating-contract")
async def get_operating_contract():
    return operating_contract()


@router.get("/service-objectives")
async def get_service_objectives():
    return service_objectives()


@router.get("/support-policy")
async def get_support_policy():
    return support_policy()


@router.get("/subprocessors")
async def get_subprocessors():
    return subprocessor_registry()


@router.get("/assurance-roadmap")
async def get_assurance_roadmap():
    return assurance_program()


@router.get("/security-review-packet")
async def download_security_review_packet():
    packet = security_review_packet()
    return JSONResponse(
        packet,
        headers={
            "Content-Disposition": (
                f'attachment; filename="lawhand-security-review-{packet["version"]}.json"'
            ),
            "ETag": f'"{packet["sha256"]}"',
            "Cache-Control": "public, max-age=300",
        },
    )
