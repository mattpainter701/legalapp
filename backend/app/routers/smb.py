"""SMB file share relay agent API endpoints."""

import asyncio
import hashlib
import json
import logging
import secrets
import uuid
from datetime import datetime, timezone

from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from pydantic import ValidationError
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, set_tenant_context
from app.middleware.smb_auth import get_smb_agent
from app.middleware.tenant import get_current_user, require_admin
from app.models.smb_agent import SmbAgent
from app.models.smb_file_index import SmbFileIndex
from app.models.smb_share import SmbShare
from app.models.native_identity import NativeIdentityMapping
from app.models.user import User
from app.schemas.smb import (
    AgentHeartbeatRequest,
    AgentInfo,
    AgentRegisterRequest,
    AgentRegisterResponse,
    AgentShareInfo,
    AgentStatusUpdate,
    ContentFetchResult,
    FirmMemorySearchHit,
    FirmMemorySearchRequest,
    FirmMemorySearchResponse,
    NativeAuthorizationStatus,
    NativeIdentityDiagnostic,
    NativeIdentityUpdate,
    MatterSmbShareCreate,
    MatterSmbShareInfo,
    PairingCodeResponse,
    ShareCreate,
    ShareInfo,
    ShareScanStatus,
    ShareUpdate,
    SmbAccessLogEntry,
    SmbCredentialCreate,
    SmbCredentialInfo,
    SmbCredentialUpdate,
    SmbSearchResult,
    SyncRequest,
    SyncResponse,
    TaskAck,
)
from app.services.smb import (
    SmbShareConflictError,
    fetch_agent_manifest,
    smb_service,
)
from app.services.smb_credentials import (
    SmbCredentialError,
    smb_credential_service,
)
from app.services.rbac_service import get_user_capabilities
from app.services.operator_audit import record_operator_audit
from app.services.token_vault import decrypt_token, encrypt_token
from app.config import get_settings
from app.schemas.file_open_intent import (
    FileOpenIntentCreate,
    FileOpenIntentCreated,
    FileOpenIntentRedeemRequest,
    FileOpenIntentRedeemed,
    FileOpenIntentOutcomeRequest,
    FileOpenIntentOutcomeResponse,
)
from app.services.file_open_intents import (
    OpenIntentError,
    create_intent,
    redeem_intent,
    record_outcome,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/smb", tags=["smb"])

_REGISTRATION_RECEIPT_PREFIX = "smb_registration_receipt:v1:"
_REGISTRATION_RECEIPT_TTL_SECONDS = 120
settings = get_settings()


async def require_firm_memory_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Require the same matter/document capabilities as Workspace MCP."""
    user = await get_current_user(request, db)
    capabilities = await get_user_capabilities(db, user.id)
    # Keep only the established administrator fallback used by other legacy
    # protected routes. Ordinary staff must hold both seeded RBAC capabilities.
    legacy_staff = str(getattr(user, "role", "")).casefold() == "admin"
    if not legacy_staff and not {
        "manage_matters",
        "manage_documents",
    }.issubset(capabilities):
        raise HTTPException(status_code=403, detail="Firm Memory access required")
    return user


@router.post("/files/open-intents", response_model=FileOpenIntentCreated)
async def create_file_open_intent(
    body: FileOpenIntentCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_firm_memory_user),
):
    try:
        intent, handle = await create_intent(
            db,
            tenant_id=str(user.tenant_id),
            user_id=str(user.id),
            file_id=body.file_id,
            matter_id=body.matter_id,
            action=body.action,
        )
    except OpenIntentError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return FileOpenIntentCreated(
        launch_url=f"lawhand-file://{intent.action}/{intent.agent_id}/{handle}",
        expires_at=intent.expires_at,
        file_id=str(intent.file_id),
        agent_id=str(intent.agent_id),
        share_id=str(intent.share_id),
        source_id=str(intent.source_id),
        file_revision=intent.revision,
        action=intent.action,
    )


@router.post(
    "/agents/{agent_id}/open-intents/redeem", response_model=FileOpenIntentRedeemed
)
async def redeem_file_open_intent(
    agent_id: str,
    body: FileOpenIntentRedeemRequest,
    db: AsyncSession = Depends(get_db),
    agent: SmbAgent = Depends(get_smb_agent),
):
    if str(agent.id) != agent_id:
        raise HTTPException(status_code=403, detail="Agent ID mismatch")
    try:
        intent = await redeem_intent(
            db,
            tenant_id=str(agent.tenant_id),
            agent_id=agent_id,
            handle=body.handle,
            action=body.action,
            session_id=body.session_id,
            user_sid=body.user_sid,
        )
    except OpenIntentError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return FileOpenIntentRedeemed(
        intent_id=str(intent.id),
        file_id=str(intent.file_id),
        source_id=str(intent.source_id),
        file_revision=str(intent.revision),
        agent_id=agent_id,
        share_id=str(intent.share_id),
        matter_id=str(intent.matter_id) if intent.matter_id else None,
        action=intent.action,
        nonce=intent.nonce,
    )


@router.post(
    "/agents/{agent_id}/open-intents/{intent_id}/outcome",
    response_model=FileOpenIntentOutcomeResponse,
)
async def file_open_intent_outcome(
    agent_id: str,
    intent_id: str,
    body: FileOpenIntentOutcomeRequest,
    db: AsyncSession = Depends(get_db),
    agent: SmbAgent = Depends(get_smb_agent),
):
    if str(agent.id) != agent_id:
        raise HTTPException(status_code=403, detail="Agent ID mismatch")
    try:
        await record_outcome(
            db,
            tenant_id=str(agent.tenant_id),
            agent_id=agent_id,
            intent_id=intent_id,
            outcome=body.outcome,
        )
    except OpenIntentError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return FileOpenIntentOutcomeResponse(status="recorded")


def _registration_receipt_key(pairing_code: str) -> str:
    """Return a non-reversible Redis key for a sensitive pairing code."""
    digest = hashlib.sha256(pairing_code.encode()).hexdigest()
    return f"{_REGISTRATION_RECEIPT_PREFIX}{digest}"


def _registration_fingerprint(body: AgentRegisterRequest) -> str:
    """Bind a retry receipt to the exact connector identity fields."""
    canonical = json.dumps(
        body.model_dump(exclude={"pairing_code"}, mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


async def _load_registration_receipt(
    redis,
    body: AgentRegisterRequest,
) -> AgentRegisterResponse | None:
    if redis is None:
        return None
    key = _registration_receipt_key(body.pairing_code)
    try:
        ciphertext = await redis.get(key)
        if not ciphertext:
            return None
        if isinstance(ciphertext, bytes):
            ciphertext = ciphertext.decode()
        envelope = json.loads(decrypt_token(ciphertext))
        expected = _registration_fingerprint(body)
        if not secrets.compare_digest(str(envelope.get("fingerprint", "")), expected):
            return None
        return AgentRegisterResponse.model_validate(envelope["response"])
    except (
        RedisError,
        InvalidToken,
        RuntimeError,
        ValueError,
        TypeError,
        KeyError,
        ValidationError,
    ):
        # Registration still works when Redis is unavailable. A malformed or
        # undecryptable receipt is never trusted as an API credential.
        logger.warning("Unable to read SMB registration retry receipt", exc_info=True)
        return None


async def _store_registration_receipt(
    redis,
    body: AgentRegisterRequest,
    response: AgentRegisterResponse,
) -> None:
    if redis is None:
        return
    try:
        envelope = json.dumps(
            {
                "fingerprint": _registration_fingerprint(body),
                "response": response.model_dump(mode="json"),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        ciphertext = encrypt_token(envelope)
        await redis.set(
            _registration_receipt_key(body.pairing_code),
            ciphertext,
            ex=_REGISTRATION_RECEIPT_TTL_SECONDS,
        )
    except (RedisError, RuntimeError, ValueError, TypeError):
        # The durable registration has already committed. Do not turn a Redis
        # outage into a false failure that encourages unsafe manual recovery.
        logger.warning("Unable to store SMB registration retry receipt", exc_info=True)


async def _wait_for_registration_receipt(
    redis,
    body: AgentRegisterRequest,
) -> AgentRegisterResponse | None:
    """Bridge the small commit-to-Redis window between concurrent retries."""
    if redis is None:
        return None
    for attempt in range(4):
        cached = await _load_registration_receipt(redis, body)
        if cached is not None:
            return cached
        if attempt < 3:
            await asyncio.sleep(0.05)
    return None


# ═══════════════════════════════════════════════════════════════════════
#  Agent-facing endpoints (API key auth)
# ═══════════════════════════════════════════════════════════════════════


@router.post("/agents/register", response_model=AgentRegisterResponse)
async def register_agent(
    body: AgentRegisterRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Validate pairing code and register a new agent. Returns API key (one time)."""
    redis = getattr(request.app.state, "redis", None)
    cached = await _load_registration_receipt(redis, body)
    if cached is not None:
        return cached

    try:
        result = await smb_service.register_agent(db, body.pairing_code, body)
    except ValueError as exc:
        # A concurrent retry can miss Redis before the first request commits,
        # then wait on the database row lock. Check once more after that wait.
        cached = await _wait_for_registration_receipt(redis, body)
        if cached is not None:
            return cached
        raise HTTPException(status_code=400, detail=str(exc))

    await db.commit()
    await _store_registration_receipt(redis, body, result)
    return result


@router.post("/agents/{agent_id}/heartbeat")
async def agent_heartbeat(
    agent_id: str,
    body: AgentHeartbeatRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    agent: SmbAgent = Depends(get_smb_agent),
):
    """Update heartbeat timestamp for an authenticated agent."""
    if str(agent.id) != agent_id:
        raise HTTPException(status_code=403, detail="Agent ID mismatch")

    await smb_service.record_heartbeat(db, agent_id, body.model_dump(exclude_none=True))
    await db.commit()
    return {"status": "ok"}


@router.post("/agents/{agent_id}/sync", response_model=SyncResponse)
async def agent_sync(
    agent_id: str,
    share_id: str = Query(..., description="Share ID being synced"),
    body: SyncRequest = Body(...),
    request: Request = None,
    db: AsyncSession = Depends(get_db),
    agent: SmbAgent = Depends(get_smb_agent),
):
    """Bulk file metadata sync from agent. Upserts files, marks deletions."""
    if str(agent.id) != agent_id:
        raise HTTPException(status_code=403, detail="Agent ID mismatch")

    tenant_id = str(agent.tenant_id)
    await set_tenant_context(db, tenant_id)
    share_result = await db.execute(
        select(SmbShare).where(
            SmbShare.id == share_id,
            SmbShare.tenant_id == agent.tenant_id,
            SmbShare.agent_id == agent.id,
        )
    )
    if share_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=403, detail="Share not found or access denied")

    result = await smb_service.sync_files(db, agent_id, tenant_id, share_id, body)
    await db.commit()
    return result


@router.get("/agents/{agent_id}/tasks")
async def get_agent_tasks(
    agent_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    agent: SmbAgent = Depends(get_smb_agent),
    limit: int = Query(10, ge=1, le=50),
    wait_seconds: int = Query(0, ge=0, le=20),
):
    """Get pending content fetch tasks for this agent."""
    if str(agent.id) != agent_id:
        raise HTTPException(status_code=403, detail="Agent ID mismatch")

    # Authentication has already loaded the device and tenant. Release that
    # transaction/connection before a long poll so idle connectors cannot
    # exhaust the database pool.
    await db.rollback()
    redis = getattr(request.app.state, "redis", None)
    tasks = await smb_service.get_pending_tasks(
        db,
        agent_id,
        redis=redis,
        limit=limit,
        wait_seconds=wait_seconds,
    )
    return tasks


@router.post("/agents/{agent_id}/tasks/{task_id}/result")
async def submit_task_result(
    agent_id: str,
    task_id: str,
    body: ContentFetchResult,
    request: Request,
    db: AsyncSession = Depends(get_db),
    agent: SmbAgent = Depends(get_smb_agent),
):
    """Submit content fetch result from agent."""
    if str(agent.id) != agent_id:
        raise HTTPException(status_code=403, detail="Agent ID mismatch")
    if body.task_id != task_id:
        raise HTTPException(status_code=400, detail="Task ID mismatch")

    redis = getattr(request.app.state, "redis", None)
    try:
        await smb_service.submit_task_result(
            db,
            agent_id,
            task_id,
            body,
            redis=redis,
            tenant_id=str(agent.tenant_id),
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except (RedisError, RuntimeError) as exc:
        logger.warning("SMB task result publication failed", exc_info=True)
        raise HTTPException(
            status_code=503, detail="SMB relay is temporarily unavailable"
        ) from exc
    return {"status": "ok"}


@router.get("/agents/{agent_id}/shares", response_model=list[AgentShareInfo])
async def list_agent_shares(
    agent_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    agent: SmbAgent = Depends(get_smb_agent),
):
    """List enabled shares for this agent, with the credential to mount each.

    This is the only response that carries a decrypted share secret. It is
    reachable only with the agent's own API key, over TLS, and a credential
    pinned to another agent is never included.
    """
    if str(agent.id) != agent_id:
        raise HTTPException(status_code=403, detail="Agent ID mismatch")

    tenant_id = str(agent.tenant_id)
    shares = await smb_service.list_agent_shares(db, agent_id, tenant_id)
    await db.commit()
    return shares


@router.post("/agents/{agent_id}/shares/{share_id}/scan-status")
async def report_scan_status(
    agent_id: str,
    share_id: str,
    body: ShareScanStatus,
    request: Request,
    db: AsyncSession = Depends(get_db),
    agent: SmbAgent = Depends(get_smb_agent),
):
    """Record the outcome of a share scan so admins can see it went through."""
    if str(agent.id) != agent_id:
        raise HTTPException(status_code=403, detail="Agent ID mismatch")

    try:
        await smb_service.record_scan_status(
            db, agent_id, str(agent.tenant_id), share_id, body
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    await db.commit()
    return {"status": "ok"}


# ═══════════════════════════════════════════════════════════════════════
#  User-facing endpoints (JWT auth)
# ═══════════════════════════════════════════════════════════════════════


@router.get("/files/search", response_model=list[SmbSearchResult])
async def search_files(
    q: str = Query(..., min_length=1),
    matter_id: str | None = Query(None),
    file_extensions: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    request: Request = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Full-text search across indexed SMB files."""
    tenant_id = str(user.tenant_id)
    ext_list = file_extensions.split(",") if file_extensions else None

    results = await smb_service.search_files(
        db, tenant_id, q, matter_id, ext_list, limit
    )
    return results


@router.post("/local-search", response_model=FirmMemorySearchResponse)
async def search_local_files(
    body: FirmMemorySearchRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_firm_memory_user),
):
    """Search matter-bound full text on the customer's outbound agent."""
    redis = getattr(request.app.state, "redis", None)
    try:
        return await smb_service.search_local_files(
            db,
            str(user.tenant_id),
            str(user.id),
            body.matter_id,
            body.query,
            body.file_extensions,
            body.limit,
            body.correlation_id,
            redis=redis,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.get("/files/{file_id}/detail", response_model=FirmMemorySearchHit)
async def get_matter_file(
    file_id: str,
    matter_id: str = Query(...),
    request: Request = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_firm_memory_user),
):
    """Resolve one safe Firm Memory deep link inside its matter binding."""
    try:
        return await smb_service.get_matter_file(
            db,
            str(user.tenant_id),
            str(user.id),
            matter_id,
            file_id,
            redis=getattr(request.app.state, "redis", None) if request else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/files/{file_id}/fetch-content")
async def request_content_fetch(
    file_id: str,
    reason: str = Query("search_result"),
    conversation_id: str | None = Query(None),
    matter_id: str | None = Query(None),
    request: Request = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Request on-demand content fetch from agent. Returns task_id for polling."""
    tenant_id = str(user.tenant_id)
    user_id = str(user.id)
    redis = getattr(request.app.state, "redis", None) if request else None

    try:
        task_id, agent_id = await smb_service.request_content_fetch(
            db,
            tenant_id,
            user_id,
            file_id,
            conversation_id,
            reason,
            redis=redis,
            matter_id=matter_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except (RedisError, RuntimeError) as exc:
        logger.warning("SMB content task publication failed", exc_info=True)
        raise HTTPException(
            status_code=503, detail="SMB relay is temporarily unavailable"
        ) from exc

    return {"task_id": task_id, "agent_id": agent_id}


@router.get("/files/{file_id}/content-status")
async def get_content_status(
    file_id: str,
    task_id: str = Query(...),
    request: Request = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Poll for content fetch result by task_id."""
    _result = await db.execute(
        select(SmbFileIndex).where(
            SmbFileIndex.id == file_id,
            SmbFileIndex.tenant_id == user.tenant_id,
            SmbFileIndex.is_deleted.is_(False),
        )
    )
    file_entry = _result.scalar_one_or_none()
    if file_entry is None:
        raise HTTPException(status_code=404, detail="File not found")

    redis = getattr(request.app.state, "redis", None) if request else None
    try:
        payload = await smb_service.get_task_result(
            task_id,
            str(user.tenant_id),
            redis=redis,
            file_id=file_id,
            kind="content_fetch",
            user_id=str(user.id),
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    if payload is None:
        return {"status": "pending"}
    if payload.get("error") or not payload.get("ok", True):
        return {
            "status": "failed",
            "error": payload.get("error") or "Agent could not read the file",
        }
    return {
        "status": "ready",
        "content": payload.get("content", ""),
        "truncated": bool(payload.get("truncated")),
    }


# ═══════════════════════════════════════════════════════════════════════
#  Admin endpoints (JWT + admin role)
# ═══════════════════════════════════════════════════════════════════════


def _identity_diagnostic(row: NativeIdentityMapping) -> NativeIdentityDiagnostic:
    return NativeIdentityDiagnostic(
        id=str(row.id),
        user_id=str(row.user_id),
        provider=row.provider,
        state=row.state,
        version=row.version,
        principal_count=1 + len(row.effective_sids or []),
        resolved_at=row.resolved_at,
        expires_at=row.expires_at,
        error_code=row.error_code,
    )


@router.get("/native-authz/status", response_model=NativeAuthorizationStatus)
async def native_authorization_status(
    db: AsyncSession = Depends(get_db), admin=Depends(require_admin)
):
    """Privacy-safe rollout diagnostics; no SIDs or object ids are returned."""
    await set_tenant_context(db, str(admin.tenant_id))
    users = (
        (
            await db.execute(
                select(User).where(
                    User.tenant_id == admin.tenant_id, User.is_active.is_(True)
                )
            )
        )
        .scalars()
        .all()
    )
    identities = (
        (
            await db.execute(
                select(NativeIdentityMapping).where(
                    NativeIdentityMapping.tenant_id == admin.tenant_id
                )
            )
        )
        .scalars()
        .all()
    )
    now = datetime.now(timezone.utc)
    healthy = sum(
        row.state == "healthy" and row.expires_at is not None and row.expires_at > now
        for row in identities
    )
    signing = bool(settings.FIRM_MEMORY_IDENTITY_TICKET_PRIVATE_KEY)
    coverage = bool(settings.FIRM_MEMORY_ACL_COVERAGE_HEALTHY)
    return NativeAuthorizationStatus(
        enabled=settings.FIRM_MEMORY_NATIVE_AUTHZ_ENABLED,
        signing_configured=signing,
        acl_coverage_confirmed=coverage,
        active_users=len(users),
        healthy_identities=healthy,
        unhealthy_identities=max(0, len(users) - healthy),
        rollout_ready=bool(users) and healthy == len(users) and signing and coverage,
    )


@router.get("/native-identities", response_model=list[NativeIdentityDiagnostic])
async def list_native_identities(
    db: AsyncSession = Depends(get_db), admin=Depends(require_admin)
):
    await set_tenant_context(db, str(admin.tenant_id))
    rows = (
        (
            await db.execute(
                select(NativeIdentityMapping)
                .where(NativeIdentityMapping.tenant_id == admin.tenant_id)
                .order_by(NativeIdentityMapping.user_id)
            )
        )
        .scalars()
        .all()
    )
    return [_identity_diagnostic(row) for row in rows]


@router.put("/native-identities/{user_id}", response_model=NativeIdentityDiagnostic)
async def update_native_identity(
    user_id: str,
    body: NativeIdentityUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin=Depends(require_admin),
):
    """Record immutable AD/Entra identity plus versioned group expansion."""
    try:
        user_uuid = uuid.UUID(user_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="User not found") from exc
    await set_tenant_context(db, str(admin.tenant_id))
    user = await db.scalar(
        select(User).where(User.id == user_uuid, User.tenant_id == admin.tenant_id)
    )
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    row = await db.scalar(
        select(NativeIdentityMapping).where(
            NativeIdentityMapping.tenant_id == admin.tenant_id,
            NativeIdentityMapping.user_id == user_uuid,
        )
    )
    immutable = (
        body.provider,
        body.directory_tenant_id,
        body.object_id,
        body.primary_sid.upper(),
    )
    if row is None:
        row = NativeIdentityMapping(
            tenant_id=admin.tenant_id,
            user_id=user_uuid,
            provider=immutable[0],
            directory_tenant_id=immutable[1],
            object_id=immutable[2],
            primary_sid=immutable[3],
        )
        db.add(row)
    elif immutable != (
        row.provider,
        row.directory_tenant_id,
        row.object_id,
        row.primary_sid.upper(),
    ):
        raise HTTPException(
            status_code=409,
            detail="Immutable native identity does not match the existing mapping",
        )
    if body.state == "healthy" and (
        not body.group_expansion_complete
        or body.expires_at is None
        or body.expires_at <= datetime.now(timezone.utc)
        or body.resolved_at is None
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                "Healthy identity requires complete group expansion and fresh "
                "resolution timestamps"
            ),
        )
    row.effective_sids = body.effective_sids
    row.state = body.state
    row.resolved_at = body.resolved_at
    row.expires_at = body.expires_at
    row.error_code = body.error_code if body.state != "healthy" else None
    row.version = int(row.version or 0) + 1
    await record_operator_audit(
        db,
        request,
        action="firm_memory.native_identity.updated",
        resource_type="native_identity_mapping",
        resource_id=user_id,
        actor_type="tenant_admin",
        actor_id=str(admin.id),
        metadata={
            "tenant_id": str(admin.tenant_id),
            "provider": body.provider,
            "state": body.state,
            "version": row.version,
            "principal_count": 1 + len(body.effective_sids),
            "group_expansion_complete": body.group_expansion_complete,
        },
    )
    await db.commit()
    await db.refresh(row)
    return _identity_diagnostic(row)


@router.post("/pairing-code", response_model=PairingCodeResponse)
async def generate_pairing_code(
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin=Depends(require_admin),
):
    """Generate a pairing code for agent registration."""
    tenant_id = str(admin.tenant_id)
    code, expires_at = await smb_service.generate_pairing_code(db, tenant_id)
    await db.commit()
    return PairingCodeResponse(pairing_code=code, expires_at=expires_at)


@router.get("/agents", response_model=list[AgentInfo])
async def list_agents(
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin=Depends(require_admin),
):
    """List all agents for this tenant."""
    tenant_id = str(admin.tenant_id)
    await set_tenant_context(db, tenant_id)

    result = await db.execute(
        select(SmbAgent).where(SmbAgent.tenant_id == admin.tenant_id)
    )
    agents = result.scalars().all()
    return [AgentInfo.model_validate(a) for a in agents]


@router.get("/agents/{agent_id}/update")
async def get_agent_update(
    agent_id: str,
    db: AsyncSession = Depends(get_db),
    admin=Depends(require_admin),
):
    """Return official latest version and this tenant agent's update state."""
    try:
        manifest = await fetch_agent_manifest()
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail="Official agent manifest unavailable"
        ) from exc
    await set_tenant_context(db, str(admin.tenant_id))
    result = await db.execute(
        select(SmbAgent).where(
            SmbAgent.id == agent_id, SmbAgent.tenant_id == admin.tenant_id
        )
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {
        "agent_id": str(agent.id),
        "current_version": agent.agent_version,
        "latest_version": manifest["target_version"],
        "manifest_id": manifest["manifest_id"],
        "update_status": agent.update_status,
        "update_target_version": agent.update_target_version,
        "update_task_id": agent.update_task_id,
        "update_requested_at": agent.update_requested_at,
        "update_completed_at": agent.update_completed_at,
        "update_error": agent.update_error,
    }


@router.post("/agents/{agent_id}/update", response_model=TaskAck)
async def request_agent_update(
    agent_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin=Depends(require_admin),
):
    """Queue the fixed official agent update; the portal supplies no URL."""
    redis = getattr(request.app.state, "redis", None)
    try:
        task_id, target_agent_id, _manifest = await smb_service.enqueue_agent_update(
            db, str(admin.tenant_id), agent_id, redis=redis
        )
    except ValueError as exc:
        detail = str(exc)
        if detail == "Agent not found":
            status = 404
        elif (
            detail == "Agent is offline"
            or detail.startswith("Agent is ")
            or detail.startswith("Agent requires ")
            or "latest" in detail
        ):
            status = 409
        else:
            status = 503
        raise HTTPException(status_code=status, detail=detail)
    return TaskAck(task_id=task_id, agent_id=target_agent_id, kind="agent_update")


@router.patch("/agents/{agent_id}", response_model=AgentInfo)
async def update_agent_status(
    agent_id: str,
    body: AgentStatusUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin=Depends(require_admin),
):
    """Update agent status (pause/resume/revoke)."""
    tenant_id = str(admin.tenant_id)
    await set_tenant_context(db, tenant_id)

    result = await db.execute(
        select(SmbAgent).where(
            SmbAgent.id == agent_id,
            SmbAgent.tenant_id == admin.tenant_id,
        )
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    valid_transitions = {
        "active": ["paused", "revoked"],
        "paused": ["active", "revoked"],
        "revoked": [],
    }
    allowed = valid_transitions.get(agent.status, [])
    if body.status not in allowed and body.status != agent.status:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot transition from {agent.status} to {body.status}",
        )

    agent.status = body.status
    await db.flush()
    await db.commit()

    await db.refresh(agent)
    return AgentInfo.model_validate(agent)


@router.delete("/agents/{agent_id}")
async def revoke_agent(
    agent_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin=Depends(require_admin),
):
    """Revoke an agent, deleting only an unregistered pairing placeholder."""
    tenant_id = str(admin.tenant_id)
    await set_tenant_context(db, tenant_id)

    result = await db.execute(
        select(SmbAgent).where(
            SmbAgent.id == agent_id,
            SmbAgent.tenant_id == admin.tenant_id,
        )
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    if agent.api_key_hash == "pending":
        # Pairing placeholders have no device credential or agent history. A
        # failed/abandoned reservation should not leave a tombstone in the
        # tenant's agent list. The service proves it owns no related state
        # before deleting, so a legacy bad association cannot cascade data.
        if await smb_service.delete_pairing_placeholder_if_empty(
            db, agent_id, tenant_id
        ):
            await db.commit()
            return {"status": "deleted", "agent_id": agent_id}

    # Registered devices remain auditable after revocation. A legacy
    # placeholder with related state is also retained instead of risking a
    # cascading delete. Clear any stale pairing material in either case.
    agent.status = "revoked"
    agent.pairing_code = None
    agent.pairing_expires_at = None
    await db.flush()
    await db.commit()
    return {"status": "revoked", "agent_id": agent_id}


@router.post("/shares", response_model=ShareInfo)
async def create_share(
    body: ShareCreate,
    agent_id: str = Query(..., description="Agent ID to assign this share to"),
    request: Request = None,
    db: AsyncSession = Depends(get_db),
    admin=Depends(require_admin),
):
    """Create a new SMB share on an agent, with its mount credential."""
    tenant_id = str(admin.tenant_id)

    agent_result = await db.execute(
        select(SmbAgent).where(
            SmbAgent.id == agent_id,
            SmbAgent.tenant_id == admin.tenant_id,
        )
    )
    if agent_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    try:
        share = await smb_service.create_share(
            db, agent_id, tenant_id, body, user_id=str(admin.id)
        )
    except SmbCredentialError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    await db.commit()

    # The tenant GUC is transaction-local, so re-establish it before reading
    # back through RLS after the commit.
    await set_tenant_context(db, tenant_id)
    await db.refresh(share)
    return await smb_service.share_info(db, share)


@router.get("/shares", response_model=list[ShareInfo])
async def list_shares(
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin=Depends(require_admin),
    agent_id: str | None = Query(None),
):
    """List all shares, optionally filtered by agent."""
    tenant_id = str(admin.tenant_id)
    await set_tenant_context(db, tenant_id)

    stmt = select(SmbShare).where(SmbShare.tenant_id == admin.tenant_id)
    if agent_id:
        stmt = stmt.where(SmbShare.agent_id == agent_id)

    result = await db.execute(stmt)
    shares = result.scalars().all()
    credential_names, agent_names = await smb_service.name_maps(db, tenant_id)
    return [
        await smb_service.share_info(db, share, credential_names, agent_names)
        for share in shares
    ]


@router.patch("/shares/{share_id}", response_model=ShareInfo)
async def update_share(
    share_id: str,
    body: ShareUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin=Depends(require_admin),
):
    """Update SMB share configuration."""
    tenant_id = str(admin.tenant_id)

    try:
        share = await smb_service.update_share(db, share_id, tenant_id, body)
    except SmbCredentialError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except SmbShareConflictError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    await db.commit()
    await set_tenant_context(db, tenant_id)
    await db.refresh(share)
    return await smb_service.share_info(db, share)


@router.delete("/shares/{share_id}")
async def delete_share(
    share_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin=Depends(require_admin),
):
    """Delete an SMB share and its file index entries."""
    tenant_id = str(admin.tenant_id)
    await smb_service.delete_share(db, share_id, tenant_id)
    await db.commit()
    return {"status": "deleted", "share_id": share_id}


@router.post("/shares/{share_id}/test-connection", response_model=TaskAck)
async def test_share_connection(
    share_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin=Depends(require_admin),
):
    """Ask the owning agent to mount the share and report back.

    Returns a task id; poll ``GET /shares/{share_id}/task/{task_id}`` for the
    outcome. The agent does the probing — the SaaS never touches the customer
    network itself.
    """
    tenant_id = str(admin.tenant_id)
    redis = getattr(request.app.state, "redis", None) if request else None

    try:
        task_id, agent_id = await smb_service.enqueue_share_task(
            db, tenant_id, share_id, "verify_share", redis=redis
        )
    except ValueError as exc:
        detail = str(exc)
        status = 404 if detail == "Share not found" else 409
        raise HTTPException(status_code=status, detail=detail)

    await db.commit()
    return TaskAck(task_id=task_id, agent_id=agent_id, kind="verify_share")


@router.post("/shares/{share_id}/scan", response_model=TaskAck)
async def scan_share_now(
    share_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin=Depends(require_admin),
):
    """Queue an immediate re-scan of a share instead of waiting for its cron."""
    tenant_id = str(admin.tenant_id)
    redis = getattr(request.app.state, "redis", None) if request else None

    try:
        task_id, agent_id = await smb_service.enqueue_share_task(
            db, tenant_id, share_id, "scan_now", redis=redis
        )
    except ValueError as exc:
        detail = str(exc)
        status = 404 if detail == "Share not found" else 409
        raise HTTPException(status_code=status, detail=detail)

    await db.commit()
    return TaskAck(task_id=task_id, agent_id=agent_id, kind="scan_now")


@router.get("/shares/{share_id}/task/{task_id}")
async def get_share_task_result(
    share_id: str,
    task_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin=Depends(require_admin),
):
    """Poll for the result of a connection test or scan request."""
    await set_tenant_context(db, str(admin.tenant_id))
    share_result = await db.execute(
        select(SmbShare).where(
            SmbShare.id == share_id,
            SmbShare.tenant_id == admin.tenant_id,
        )
    )
    if share_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Share not found")

    redis = getattr(request.app.state, "redis", None) if request else None
    try:
        payload = await smb_service.get_task_result(
            task_id,
            str(admin.tenant_id),
            redis=redis,
            share_id=share_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    if payload is None:
        return {"status": "pending"}
    return {
        "status": "ok" if payload.get("ok") else "failed",
        "error": payload.get("error"),
        "detail": payload.get("detail"),
    }


# ── Credential vault (admin) ────────────────────────────────────────────────


def _credential_info(credential, share_counts: dict[str, int]) -> SmbCredentialInfo:
    """Project a credential row into its admin view (never the secret)."""
    info = SmbCredentialInfo.model_validate(credential)
    return info.model_copy(
        update={
            "has_password": bool(credential.encrypted_password),
            "agent_id": (str(credential.agent_id) if credential.agent_id else None),
            "share_count": share_counts.get(str(credential.id), 0),
        }
    )


@router.post("/credentials", response_model=SmbCredentialInfo)
async def create_credential(
    body: SmbCredentialCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin=Depends(require_admin),
):
    """Store a file share credential for this tenant (secret encrypted at rest)."""
    tenant_id = str(admin.tenant_id)
    try:
        credential = await smb_credential_service.create_credential(
            db, tenant_id, body, user_id=str(admin.id)
        )
    except SmbCredentialError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    await db.commit()
    await set_tenant_context(db, tenant_id)
    await db.refresh(credential)
    return _credential_info(credential, {})


@router.get("/credentials", response_model=list[SmbCredentialInfo])
async def list_credentials(
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin=Depends(require_admin),
):
    """List stored credentials. Secrets are never returned."""
    tenant_id = str(admin.tenant_id)
    credentials = await smb_credential_service.list_credentials(db, tenant_id)
    share_counts = await smb_credential_service.share_counts(db, tenant_id)
    return [_credential_info(c, share_counts) for c in credentials]


@router.patch("/credentials/{credential_id}", response_model=SmbCredentialInfo)
async def update_credential(
    credential_id: str,
    body: SmbCredentialUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin=Depends(require_admin),
):
    """Update a credential. Omit ``password`` to keep the stored secret."""
    tenant_id = str(admin.tenant_id)
    try:
        credential = await smb_credential_service.update_credential(
            db, credential_id, tenant_id, body
        )
    except SmbCredentialError as exc:
        detail = str(exc)
        status = 404 if detail == "Credential not found" else 400
        raise HTTPException(status_code=status, detail=detail)

    await db.commit()
    await set_tenant_context(db, tenant_id)
    await db.refresh(credential)
    share_counts = await smb_credential_service.share_counts(db, tenant_id)
    return _credential_info(credential, share_counts)


@router.delete("/credentials/{credential_id}")
async def delete_credential(
    credential_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin=Depends(require_admin),
):
    """Delete a credential; shares that used it fall back to the agent identity."""
    tenant_id = str(admin.tenant_id)
    try:
        detached = await smb_credential_service.delete_credential(
            db, credential_id, tenant_id
        )
    except SmbCredentialError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    await db.commit()
    return {
        "status": "deleted",
        "credential_id": credential_id,
        "detached_shares": detached,
    }


@router.get("/stats")
async def get_stats(
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin=Depends(require_admin),
):
    """Admin dashboard stats for SMB feature."""
    tenant_id = str(admin.tenant_id)
    stats = await smb_service.get_admin_stats(db, tenant_id)
    return stats


@router.get("/access-log", response_model=list[SmbAccessLogEntry])
async def get_access_log(
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin=Depends(require_admin),
    limit: int = Query(100, ge=1, le=500),
):
    """Recent content fetch access log for audit."""
    tenant_id = str(admin.tenant_id)
    from app.models.smb_access_log import SmbAccessLog as SmbAccessLogModel

    await set_tenant_context(db, tenant_id)
    result = await db.execute(
        select(SmbAccessLogModel)
        .where(SmbAccessLogModel.tenant_id == admin.tenant_id)
        .order_by(SmbAccessLogModel.accessed_at.desc())
        .limit(limit)
    )
    entries = result.scalars().all()
    return [SmbAccessLogEntry.model_validate(e) for e in entries]


# ═══════════════════════════════════════════════════════════════════════
#  Matter binding endpoints (JWT auth)
# ═══════════════════════════════════════════════════════════════════════


@router.post("/matters/{matter_id}/smb-shares", response_model=MatterSmbShareInfo)
async def create_matter_binding(
    matter_id: str,
    body: MatterSmbShareCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Bind an SMB share/folder to a matter."""
    tenant_id = str(user.tenant_id)

    try:
        binding = await smb_service.create_matter_binding(
            db, matter_id, tenant_id, body
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    await db.commit()

    share_result = await db.execute(
        select(SmbShare).where(SmbShare.id == body.share_id)
    )
    share = share_result.scalar_one_or_none()

    info = MatterSmbShareInfo.model_validate(binding)
    info = info.model_copy(update={"share_path": share.share_path if share else None})
    return info


@router.get("/matters/{matter_id}/smb-shares", response_model=list[MatterSmbShareInfo])
async def list_matter_bindings(
    matter_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """List SMB bindings for a matter."""
    tenant_id = str(user.tenant_id)
    bindings = await smb_service.list_matter_bindings(db, matter_id, tenant_id)

    results = []
    for b in bindings:
        share_result = await db.execute(
            select(SmbShare).where(SmbShare.id == b.share_id)
        )
        share = share_result.scalar_one_or_none()
        info = MatterSmbShareInfo.model_validate(b)
        info = info.model_copy(
            update={"share_path": share.share_path if share else None}
        )
        results.append(info)
    return results


@router.delete("/matters/{matter_id}/smb-shares/{binding_id}")
async def delete_matter_binding(
    matter_id: str,
    binding_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Remove an SMB share binding from a matter."""
    tenant_id = str(user.tenant_id)
    await smb_service.delete_matter_binding(db, binding_id, tenant_id)
    await db.commit()
    return {"status": "deleted", "binding_id": binding_id}
