"""SMB file share service — agent pairing, sync, search, content fetch."""

import asyncio
import re
import hashlib
import json
import logging
import math
import secrets
import time
import uuid
from urllib.parse import urljoin, urlparse
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, delete, func, or_ as sa_or, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import (
    clear_smb_agent_bootstrap_lookup,
    set_smb_agent_bootstrap_lookup,
    set_tenant_context,
)
from app.models.matter_smb_share import MatterSmbShare
from app.models.plugin import Matter
from app.models.smb_access_log import SmbAccessLog
from app.models.smb_agent import SmbAgent
from app.models.smb_credential import SmbCredential
from app.models.smb_file_index import SmbFileIndex
from app.models.smb_share import SmbShare
from app.services.cloud_init import MATTER_SUBFOLDERS, matter_relative_path
from app.services.corpus_revision import advance_rag_corpus_revision
from app.schemas.smb import (
    AgentRegisterRequest,
    AgentRegisterResponse,
    AgentShareCredential,
    AgentShareInfo,
    ContentFetchResult,
    ContentFetchTask,
    FirmMemoryAgentStatus,
    FirmMemorySearchHit,
    FirmMemorySearchResponse,
    LocalSearchResultDetail,
    MatterSmbShareCreate,
    ShareCreate,
    ShareInfo,
    ShareScanStatus,
    ShareUpdate,
    SmbSearchResult,
    SyncRequest,
    SyncResponse,
)
from app.services.smb_credentials import (
    SmbCredentialError,
    smb_credential_service,
)
from app.services.native_authorization import (
    NativeAuthorizationError,
    require_matter_authorization,
    resolve_native_identity,
)
from app.services.search_identity_ticket import (
    SearchIdentity,
    mint_search_identity_ticket,
)

settings = get_settings()
logger = logging.getLogger(__name__)

SMB_PAIRING_CODE_TTL_MIN = settings.SMB_PAIRING_CODE_TTL_MIN
SMB_SNIPPET_MAX_CHARS = settings.SMB_SNIPPET_MAX_CHARS
SMB_MAX_FILE_INDEX_PER_SHARE = settings.SMB_MAX_FILE_INDEX_PER_SHARE
REDIS_TASK_TTL = 300  # 5 minutes
LOCAL_SEARCH_TIMEOUT_SECONDS = 12.0
LOCAL_SEARCH_POLL_SECONDS = 0.15
# Pairing codes are read off a screen and typed into an installer command line,
# so they use an alphabet without look-alike characters and are grouped in
# fours. Sixteen symbols from thirty is ~78 bits, and the code both expires and
# is rate limited at the registration endpoint.
PAIRING_CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"
PAIRING_CODE_SYMBOLS = 16
# Operational tasks (connection test, scan now) wait longer than a content
# fetch because an agent polls on its own cadence and a scan is not instant.
REDIS_ADMIN_TASK_TTL = 900  # 15 minutes
ADMIN_TASK_KINDS = ("verify_share", "scan_now")
AGENT_UPDATE_TASK_KIND = "agent_update"
AGENT_OFFLINE_AFTER_SECONDS = 900
AGENT_PORTAL_UPDATE_MIN_VERSION = (0, 15, 0)
AGENT_UPDATE_TIMEOUT_SECONDS = 30 * 60
OFFICIAL_AGENT_MANIFEST_URL = "https://github.com/mattpainter701/legalapp/releases/latest/download/agent-update.json"
AGENT_UPDATE_MANIFEST_MAX_BYTES = 16 * 1024
_manifest_cache: tuple[float, dict] | None = None
_manifest_failure_until = 0.0
AGENT_UPDATE_MANIFEST_FAILURE_BACKOFF_SECONDS = 30
AGENT_MANIFEST_MAX_REDIRECTS = 5


class SmbShareConflictError(ValueError):
    """Raised when a tenant already has the requested agent/path share."""


OFFICIAL_MANIFEST_REDIRECT_HOSTS = {
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
    "github-releases.githubusercontent.com",
}
_GITHUB_MANIFEST_REDIRECT_PATH = re.compile(
    r"^/mattpainter701/legalapp/releases/(?:latest/download/agent-update\.json|"
    r"download/agent-v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)/"
    r"(?:agent-update\.json|lawhand-agent-x64\.msi|"
    r"lawhand-agent-linux-x86_64\.tar\.gz))$"
)


def _is_official_manifest_redirect(url: str) -> bool:
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        parsed.port
    except ValueError:
        return False
    return bool(
        parsed.scheme == "https"
        and hostname
        and hostname.casefold() in OFFICIAL_MANIFEST_REDIRECT_HOSTS
        and not parsed.username
        and not parsed.password
        and not parsed.fragment
        and (
            hostname.casefold() != "github.com"
            or (
                not parsed.query
                and _GITHUB_MANIFEST_REDIRECT_PATH.fullmatch(parsed.path)
            )
        )
    )


def _version_key(value: str) -> tuple[int, ...]:
    match = re.fullmatch(
        r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)",
        (value or "").strip(),
    )
    if not match:
        raise ValueError("Invalid agent version")
    return tuple(int(part or 0) for part in match.groups())


def expire_stale_agent_update(agent: SmbAgent, *, now: datetime | None = None) -> bool:
    """Fail a portal request that never produced a confirming heartbeat."""
    if agent.update_status not in {"queued", "in_progress"}:
        return False
    if not agent.update_requested_at or not agent.update_target_version:
        return False
    now = now or datetime.now(timezone.utc)
    if (
        now - agent.update_requested_at
    ).total_seconds() <= AGENT_UPDATE_TIMEOUT_SECONDS:
        return False
    try:
        if agent.agent_version and _version_key(agent.agent_version) >= _version_key(
            agent.update_target_version
        ):
            return False
    except ValueError:
        pass
    agent.update_status = "failed"
    agent.update_error = (
        "The agent did not confirm the update within 30 minutes; check its service logs"
    )
    return True


async def restore_queued_agent_update(
    agent: SmbAgent, tenant_id: str, redis, *, manifest: dict
) -> bool:
    """Re-publish a durable reservation only for the current official release."""
    if not redis or agent.update_status != "queued":
        return False
    if not (
        agent.update_task_id
        and agent.update_target_version
        and agent.update_manifest_id
    ):
        return False
    if (
        agent.update_target_version != manifest["target_version"]
        or agent.update_manifest_id != manifest["manifest_id"]
    ):
        agent.update_status = "failed"
        agent.update_error = (
            "The queued release is no longer the official release; "
            "request the update again"
        )
        return False
    pending_key = f"smb_task_pending:{agent.id}:{agent.update_task_id}"
    if await redis.exists(pending_key):
        return False
    task = ContentFetchTask(
        task_id=agent.update_task_id,
        kind=AGENT_UPDATE_TASK_KIND,
        target_version=agent.update_target_version,
        manifest_id=agent.update_manifest_id,
    )
    await redis.set(
        pending_key,
        json.dumps(
            {
                **task.model_dump(),
                "tenant_id": tenant_id,
                "agent_id": str(agent.id),
            }
        ),
        ex=REDIS_ADMIN_TASK_TTL,
    )
    return True


async def reconcile_queued_agent_updates(db: AsyncSession, redis) -> int:
    """Re-publish durable update reservations that Redis no longer has.

    The reservation in ``smb_agents`` is authoritative.  Before restoring it,
    compare its pinned release identity with the currently published official
    manifest; an old task must never be replayed against a newer release. This
    function owns its short database transaction so no row lock spans Redis I/O.
    """
    manifest = None
    try:
        manifest = await fetch_agent_manifest()
    except Exception:
        logger.warning(
            "SMB update reconciliation could not read the official manifest",
            exc_info=True,
        )

    result = await db.execute(
        select(SmbAgent)
        .where(SmbAgent.update_status.in_(("queued", "in_progress")))
        .with_for_update(skip_locked=True)
    )
    restore_candidates: list[SmbAgent] = []
    for agent in result.scalars().all():
        if expire_stale_agent_update(agent):
            continue
        if agent.update_status != "queued" or manifest is None:
            continue
        if not agent.update_task_id:
            continue
        if (
            agent.update_target_version != manifest["target_version"]
            or agent.update_manifest_id != manifest["manifest_id"]
        ):
            agent.update_status = "failed"
            agent.update_error = (
                "The queued release is no longer the official release; "
                "request the update again"
            )
            continue
        restore_candidates.append(agent)

    await db.flush()
    await db.commit()

    if not redis or manifest is None:
        return 0
    restored = 0
    for agent in restore_candidates:
        try:
            if await restore_queued_agent_update(
                agent, str(agent.tenant_id), redis, manifest=manifest
            ):
                restored += 1
        except Exception:
            # Leave the durable reservation queued.  The next reconciliation
            # tick can retry after a transient Redis failure.
            logger.warning(
                "Could not restore SMB update task %s",
                agent.update_task_id,
                exc_info=True,
            )
    return restored


async def fetch_agent_manifest() -> dict:
    """Fetch and validate the fixed manifest with success/failure caching."""
    global _manifest_cache, _manifest_failure_until
    now = time.monotonic()
    if (
        _manifest_cache
        and now - _manifest_cache[0] < settings.SMB_AGENT_MANIFEST_CACHE_SECONDS
    ):
        return _manifest_cache[1]
    if now < _manifest_failure_until:
        raise RuntimeError("Official agent manifest is temporarily unavailable")
    try:
        manifest = await _fetch_agent_manifest_uncached()
    except Exception:
        _manifest_failure_until = now + AGENT_UPDATE_MANIFEST_FAILURE_BACKOFF_SECONDS
        raise
    _manifest_cache = (now, manifest)
    _manifest_failure_until = 0.0
    return manifest


async def _fetch_agent_manifest_uncached() -> dict:
    """Download and strictly validate the official GitHub release manifest."""
    import httpx

    current_url = OFFICIAL_AGENT_MANIFEST_URL
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
        for hop in range(AGENT_MANIFEST_MAX_REDIRECTS + 1):
            async with client.stream(
                "GET",
                current_url,
                headers={"Accept": "application/vnd.github+json"},
            ) as response:
                status = getattr(response, "status_code", 200)
                if status in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if hop >= AGENT_MANIFEST_MAX_REDIRECTS or not location:
                        raise ValueError(
                            "Official agent manifest redirect limit exceeded"
                        )
                    current_url = urljoin(current_url, location)
                    if not _is_official_manifest_redirect(current_url):
                        raise ValueError(
                            "Official agent manifest redirected to an untrusted host"
                        )
                    continue
                response.raise_for_status()
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > AGENT_UPDATE_MANIFEST_MAX_BYTES:
                        raise ValueError("Agent release manifest is too large")
                break
        else:
            raise ValueError("Official agent manifest redirect limit exceeded")
    payload = json.loads(content)
    if not isinstance(payload, dict):
        raise ValueError("Agent release manifest is not an object")
    if set(payload) != {"schema_version", "version", "assets"}:
        raise ValueError("Agent release manifest contains unexpected fields")
    if payload.get("schema_version") != 1:
        raise ValueError("Unsupported agent release manifest schema")
    version = str(payload.get("version") or "")
    _version_key(version)
    assets = payload.get("assets")
    if not isinstance(assets, dict):
        raise ValueError("Official release has no assets")
    normalized = {}
    for platform, name in {
        "windows-x86_64": "lawhand-agent-x64.msi",
        "linux-x86_64": "lawhand-agent-linux-x86_64.tar.gz",
    }.items():
        asset = assets.get(platform)
        if not isinstance(asset, dict):
            raise ValueError(f"Official release is missing {platform} asset")
        digest = str(asset.get("sha256") or "")
        if asset.get("name") != name:
            raise ValueError("Official release contains an invalid asset name")
        if set(asset) != {"name", "sha256"}:
            raise ValueError("Official release contains unexpected asset fields")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            raise ValueError("Official release contains an invalid checksum")
        normalized[platform] = {"name": name, "sha256": digest.lower()}
    manifest_id = f"agent-v{version}"
    return {
        "manifest_id": manifest_id,
        "target_version": version,
        "assets": normalized,
    }


def _uuid(val: str | uuid.UUID | None) -> uuid.UUID | None:
    """Coerce a string or UUID value to uuid.UUID, returning None for None/empty."""
    if val is None:
        return None
    if isinstance(val, uuid.UUID):
        return val
    return uuid.UUID(str(val))


def _normalize_extensions(exts: list[str] | None) -> list[str] | None:
    """Accept ``pdf``/``.PDF``/`` .pdf `` and store a canonical ``.pdf``."""
    if exts is None:
        return None
    cleaned = []
    for raw in exts:
        item = (raw or "").strip().lower()
        if not item:
            continue
        if not item.startswith("."):
            item = f".{item}"
        if item not in cleaned:
            cleaned.append(item)
    return cleaned or None


def _parse_unc(share_path: str) -> tuple[str | None, str | None, str | None]:
    """Split ``\\\\server\\share\\sub\\dir`` into its parts.

    Returns ``(server, share, root_path)``; any part that cannot be determined
    comes back as ``None`` so the agent can fall back to the raw path.
    """
    normalized = (share_path or "").replace("/", "\\").strip()
    if not normalized.startswith("\\\\"):
        return None, None, None
    parts = [p for p in normalized.lstrip("\\").split("\\") if p]
    if len(parts) < 2:
        return None, None, None
    server, share = parts[0], parts[1]
    root = "\\".join(parts[2:]) if len(parts) > 2 else None
    return server, share, root


def _normalize_folder_path(folder_path: str | None) -> str | None:
    """Return a safe share-relative folder path, or ``None`` for the root."""
    raw = (folder_path or "").strip().replace("\\", "/")
    parts = [part for part in raw.split("/") if part and part != "."]
    if any(part == ".." for part in parts):
        raise ValueError("Folder path must stay within the assigned share")
    normalized = "/".join(parts)
    return normalized or None


def _path_is_within_binding(
    file_path: str, share_path: str, folder_path: str | None
) -> bool:
    """Match one file to the exact bound share folder without sibling widening."""
    share_root = (share_path or "").replace("/", "\\").rstrip("\\").casefold()
    candidate = (file_path or "").replace("/", "\\").rstrip("\\").casefold()
    folder = _normalize_folder_path(folder_path)
    root = share_root
    if folder:
        root += "\\" + folder.replace("/", "\\").casefold()
    return bool(root) and (candidate == root or candidate.startswith(root + "\\"))


def _escape_like(value: str, escape: str = "!") -> str:
    """Escape user-controlled path text for a SQL LIKE/ILIKE pattern."""
    return (
        value.replace(escape, escape + escape)
        .replace("%", escape + "%")
        .replace("_", escape + "_")
    )


def _pairing_code() -> str:
    """Return a 19-character grouped pairing code (fits smb_agents.pairing_code)."""
    raw = "".join(
        secrets.choice(PAIRING_CODE_ALPHABET) for _ in range(PAIRING_CODE_SYMBOLS)
    )
    return "-".join(raw[i : i + 4] for i in range(0, PAIRING_CODE_SYMBOLS, 4))


def _placeholder_has_no_related_state():
    """SQL predicates proving a pairing reservation owns no durable state."""
    return (
        ~select(SmbShare.id)
        .where(SmbShare.agent_id == SmbAgent.id)
        .correlate(SmbAgent)
        .exists(),
        ~select(SmbCredential.id)
        .where(SmbCredential.agent_id == SmbAgent.id)
        .correlate(SmbAgent)
        .exists(),
        ~select(SmbFileIndex.id)
        .where(SmbFileIndex.agent_id == SmbAgent.id)
        .correlate(SmbAgent)
        .exists(),
        ~select(SmbAccessLog.id)
        .where(SmbAccessLog.agent_id == SmbAgent.id)
        .correlate(SmbAgent)
        .exists(),
    )


async def _commit_audit_then_publish(
    db: AsyncSession,
    redis,
    pending_key: str,
    task_payload: dict,
) -> None:
    """Make the audit row durable before an agent can observe the task."""
    serialized = json.dumps(task_payload)
    await db.commit()
    await redis.set(
        pending_key,
        serialized,
        ex=REDIS_TASK_TTL,
    )


async def _commit_result_then_publish(
    db: AsyncSession,
    redis,
    result_key: str,
    pending_key: str,
    payload: str,
    ttl: int,
) -> None:
    """Make result-side audit updates durable before exposing content."""
    await db.commit()
    await redis.set(result_key, payload, ex=ttl)
    await redis.delete(pending_key)


class SmbService:
    """Business logic for SMB file share relay agent operations."""

    async def generate_pairing_code(
        self,
        db: AsyncSession,
        tenant_id: str,
    ) -> tuple[str, datetime]:
        """Generate a pairing code and store on a new SmbAgent row (status='pending').

        Returns (code, expires_at).
        """
        await set_tenant_context(db, tenant_id)

        code = _pairing_code()
        expires_at = datetime.now(timezone.utc) + timedelta(
            minutes=SMB_PAIRING_CODE_TTL_MIN
        )

        agent = SmbAgent(
            tenant_id=_uuid(tenant_id),
            agent_name=f"pending-{code[:4].lower()}",
            api_key_hash="pending",
            status="pending",
            pairing_code=code,
            pairing_expires_at=expires_at,
        )
        db.add(agent)
        await db.flush()

        return code, expires_at

    async def cleanup_expired_pairing_agents(
        self,
        db: AsyncSession,
        tenant_id: str,
        *,
        now: datetime | None = None,
    ) -> int:
        """Remove unregistered pairing placeholders after their reservation expires.

        A placeholder has no API credential and cannot own useful agent state.
        Restrict this cleanup to the sentinel hash written by
        :meth:`generate_pairing_code`; registered agents (including revoked
        ones) are never eligible. The caller supplies normal tenant RLS.
        """
        await set_tenant_context(db, tenant_id)
        cutoff = now or datetime.now(timezone.utc)
        result = await db.execute(
            delete(SmbAgent).where(
                SmbAgent.api_key_hash == "pending",
                SmbAgent.tenant_id == _uuid(tenant_id),
                sa_or(
                    # A live pending reservation is retained until its code
                    # expires; revoked placeholders are already unusable and
                    # can be removed immediately (including legacy rows whose
                    # expiry field was cleared or never populated).
                    and_(
                        SmbAgent.status == "pending",
                        SmbAgent.pairing_expires_at.is_not(None),
                        SmbAgent.pairing_expires_at <= cutoff,
                    ),
                    SmbAgent.status == "revoked",
                ),
                *_placeholder_has_no_related_state(),
            )
        )
        await db.commit()
        return int(result.rowcount or 0)

    async def delete_pairing_placeholder_if_empty(
        self,
        db: AsyncSession,
        agent_id: str,
        tenant_id: str,
    ) -> bool:
        """Delete one never-registered reservation only when it owns no state."""
        await set_tenant_context(db, tenant_id)
        result = await db.execute(
            delete(SmbAgent)
            .where(
                SmbAgent.id == _uuid(agent_id),
                SmbAgent.tenant_id == _uuid(tenant_id),
                SmbAgent.api_key_hash == "pending",
                *_placeholder_has_no_related_state(),
            )
            .execution_options(synchronize_session=False)
        )
        return bool(result.rowcount)

    async def register_agent(
        self,
        db: AsyncSession,
        pairing_code: str,
        agent_info: AgentRegisterRequest,
        tenant_id: str | None = None,
    ) -> AgentRegisterResponse:
        """Validate pairing code, register agent, return agent_id + raw API key.

        The raw API key is only returned once on registration.
        If tenant_id is provided, the pairing code lookup is scoped to that tenant.
        """
        stmt = select(SmbAgent).where(SmbAgent.pairing_code == pairing_code)
        if tenant_id:
            stmt = stmt.where(SmbAgent.tenant_id == _uuid(tenant_id))
        await set_smb_agent_bootstrap_lookup(db, pairing_code=pairing_code)
        try:
            result = await db.execute(stmt)
        finally:
            await clear_smb_agent_bootstrap_lookup(db)
        agent = result.scalar_one_or_none()

        if agent is None:
            raise ValueError("Invalid pairing code")

        # A SELECT ... FOR UPDATE also requires the table's UPDATE/ALL RLS
        # policy, so it cannot be part of the pre-tenant bootstrap lookup.
        # Bind the discovered tenant first, then take the lock under ordinary
        # tenant RLS before changing the row. This closes the registration
        # race without granting a bootstrap write policy.
        await set_tenant_context(db, str(agent.tenant_id))
        locked_result = await db.execute(
            select(SmbAgent)
            .where(
                SmbAgent.id == agent.id,
                SmbAgent.tenant_id == agent.tenant_id,
                SmbAgent.pairing_code == pairing_code,
            )
            .with_for_update()
        )
        agent = locked_result.scalar_one_or_none()
        if agent is None:
            raise ValueError("Invalid pairing code")
        if agent.status != "pending":
            raise ValueError("Pairing code already used")
        if agent.pairing_expires_at and agent.pairing_expires_at < datetime.now(
            timezone.utc
        ):
            raise ValueError("Pairing code expired")

        raw_api_key = secrets.token_urlsafe(32)
        key_hash = hashlib.sha256(raw_api_key.encode()).hexdigest()

        agent.agent_name = agent_info.agent_name
        agent.agent_version = agent_info.agent_version
        agent.hostname = agent_info.hostname
        agent.os_info = agent_info.os_info
        agent.api_key_hash = key_hash
        agent.status = "active"
        agent.pairing_code = None
        agent.pairing_expires_at = None

        await db.flush()

        return AgentRegisterResponse(
            agent_id=str(agent.id),
            api_key=raw_api_key,
        )

    async def record_heartbeat(
        self,
        db: AsyncSession,
        agent_id: str,
        data: dict,
    ) -> None:
        """Update heartbeat timestamp and optional agent metadata."""
        values: dict = {"last_heartbeat": datetime.now(timezone.utc)}
        if data.get("agent_version"):
            values["agent_version"] = data["agent_version"]
        if data.get("hostname"):
            values["hostname"] = data["hostname"]

        await db.execute(
            update(SmbAgent).where(SmbAgent.id == _uuid(agent_id)).values(**values)
        )
        result = await db.execute(
            select(SmbAgent).where(SmbAgent.id == _uuid(agent_id))
        )
        agent = result.scalar_one_or_none()
        if agent and agent.update_target_version and agent.agent_version:
            reported_target = data.get("update_target_version")
            reported_status = data.get("update_status")
            explicit_failure = (
                reported_target == agent.update_target_version
                and reported_status == "failed"
            )
            if reported_target == agent.update_target_version:
                if explicit_failure:
                    agent.update_status = "failed"
                    agent.update_error = (
                        data.get("update_error") or "The managed agent update failed"
                    )[:2000]
                elif (
                    reported_status == "in_progress" and agent.update_status == "queued"
                ):
                    agent.update_status = "in_progress"
            # A failed heartbeat is authoritative for this attempt.  Do not
            # let a simultaneously reported/raced version erase that failure.
            if not explicit_failure and agent.update_status != "failed":
                try:
                    if _version_key(agent.agent_version) >= _version_key(
                        agent.update_target_version
                    ):
                        agent.update_status = "completed"
                        agent.update_completed_at = datetime.now(timezone.utc)
                        agent.update_error = None
                except ValueError:
                    pass
        await db.flush()

    async def enqueue_agent_update(
        self, db: AsyncSession, tenant_id: str, agent_id: str, redis=None
    ) -> tuple[str, str, dict]:
        """Queue a tenant-bound update using only the official cached manifest."""
        if not redis:
            raise ValueError("Task queue unavailable; cannot reach the agent")
        await set_tenant_context(db, tenant_id)
        try:
            manifest = await fetch_agent_manifest()
        except Exception as exc:
            raise ValueError("Official agent manifest unavailable") from exc
        result = await db.execute(
            select(SmbAgent)
            .where(
                SmbAgent.id == _uuid(agent_id),
                SmbAgent.tenant_id == _uuid(tenant_id),
            )
            .with_for_update()
        )
        agent = result.scalar_one_or_none()
        if agent is None:
            raise ValueError("Agent not found")
        if agent.status != "active":
            raise ValueError(f"Agent is {agent.status}")
        if (
            not agent.last_heartbeat
            or (datetime.now(timezone.utc) - agent.last_heartbeat).total_seconds()
            > AGENT_OFFLINE_AFTER_SECONDS
        ):
            raise ValueError("Agent is offline")
        target = manifest["target_version"]
        if not agent.agent_version:
            raise ValueError(
                "Agent requires one manual upgrade to 0.15.0 before portal updates"
            )
        try:
            current_version = _version_key(agent.agent_version)
            if current_version < AGENT_PORTAL_UPDATE_MIN_VERSION:
                raise ValueError(
                    "Agent requires one manual upgrade to 0.15.0 before portal updates"
                )
            if _version_key(target) <= current_version:
                raise ValueError("Agent is already at the latest version")
        except ValueError as exc:
            if str(exc) in {
                "Agent is already at the latest version",
                "Agent requires one manual upgrade to 0.15.0 before portal updates",
            }:
                raise
            raise ValueError("Agent has an invalid current version") from exc
        expire_stale_agent_update(agent)
        if (
            agent.update_status == "in_progress"
            and agent.update_target_version == target
            and agent.update_task_id
        ):
            return agent.update_task_id, str(agent.id), manifest
        if (
            agent.update_status == "queued"
            and agent.update_target_version == target
            and agent.update_task_id
        ):
            # Release the row lock before consulting Redis. The durable row is
            # authoritative, so a cache outage remains retryable by the
            # reconciliation job instead of holding heartbeat/admin writes.
            await db.commit()
            try:
                await restore_queued_agent_update(
                    agent, tenant_id, redis, manifest=manifest
                )
            except Exception as exc:
                raise ValueError(
                    "Task queue unavailable; cannot reach the agent"
                ) from exc
            return agent.update_task_id, str(agent.id), manifest

        task_id = secrets.token_urlsafe(16)
        task = ContentFetchTask(
            task_id=task_id,
            kind=AGENT_UPDATE_TASK_KIND,
            target_version=target,
            manifest_id=manifest["manifest_id"],
        )
        agent.update_status = "queued"
        agent.update_target_version = target
        agent.update_manifest_id = manifest["manifest_id"]
        agent.update_task_id = task_id
        agent.update_requested_at = datetime.now(timezone.utc)
        agent.update_completed_at = None
        agent.update_error = None
        await db.flush()
        await db.commit()
        try:
            await redis.set(
                f"smb_task_pending:{agent.id}:{task_id}",
                json.dumps(
                    {
                        **task.model_dump(),
                        "tenant_id": tenant_id,
                        "agent_id": str(agent.id),
                    }
                ),
                ex=REDIS_ADMIN_TASK_TTL,
            )
        except Exception as exc:
            agent.update_status = "failed"
            agent.update_error = "Task queue unavailable"
            await db.commit()
            raise ValueError("Task queue unavailable; cannot reach the agent") from exc
        return task_id, str(agent.id), manifest

    async def sync_files(
        self,
        db: AsyncSession,
        agent_id: str,
        tenant_id: str,
        share_id: str,
        sync_data: SyncRequest,
    ) -> SyncResponse:
        """Upsert file metadata into smb_file_index. Mark deletions as is_deleted=True.

        Uses PostgreSQL INSERT ... ON CONFLICT DO UPDATE. Auto-generates
        search_vector from snippet + filename. Capped at
        SMB_MAX_FILE_INDEX_PER_SHARE per share.
        """
        await set_tenant_context(db, tenant_id)

        tenant_uuid = _uuid(tenant_id)
        share_uuid = _uuid(share_id)
        agent_uuid = _uuid(agent_id)

        synced = 0
        errors: list[dict] = []

        await db.flush()  # flush pending inserts to get accurate count

        count_result = await db.execute(
            select(func.count(SmbFileIndex.id)).where(
                SmbFileIndex.tenant_id == tenant_uuid,
                SmbFileIndex.share_id == share_uuid,
                SmbFileIndex.is_deleted.is_(False),
            )
        )
        current_count = count_result.scalar_one()

        # The cap is a guardrail on active rows, not a reason to reject updates
        # to files already in the index.  The previous implementation bumped
        # ``current_count`` for every upsert, so a normal change-only batch
        # could hit the cap even when it added no rows (and, once at the cap,
        # existing files could never be refreshed).
        incoming_paths = list(dict.fromkeys(entry.path for entry in sync_data.files))
        existing_states: dict[str, bool] = {}
        if incoming_paths:
            existing_result = await db.execute(
                select(SmbFileIndex.path, SmbFileIndex.is_deleted).where(
                    SmbFileIndex.tenant_id == tenant_uuid,
                    SmbFileIndex.share_id == share_uuid,
                    SmbFileIndex.path.in_(incoming_paths),
                )
            )
            existing_states = {
                str(path): bool(is_deleted)
                for path, is_deleted in existing_result.all()
            }
        activated_paths: set[str] = set()

        snippet_cap = SMB_SNIPPET_MAX_CHARS

        for entry in sync_data.files:
            try:
                existing_is_deleted = existing_states.get(entry.path)
                activates_row = entry.path not in existing_states or existing_is_deleted
                if (
                    activates_row
                    and entry.path not in activated_paths
                    and current_count >= SMB_MAX_FILE_INDEX_PER_SHARE
                ):
                    errors.append(
                        {
                            "path": entry.path,
                            "error": "Share file index cap reached",
                        }
                    )
                    continue

                snippet_val = entry.snippet[:snippet_cap] if entry.snippet else None
                search_text = f"{entry.filename} {snippet_val or ''}".strip()

                stmt = pg_insert(SmbFileIndex).values(
                    tenant_id=tenant_uuid,
                    share_id=share_uuid,
                    agent_id=agent_uuid,
                    path=entry.path,
                    filename=entry.filename,
                    ext=entry.ext,
                    mime_type=entry.mime_type,
                    snippet=snippet_val,
                    owner=entry.owner,
                    size_bytes=entry.size_bytes,
                    modified_time=entry.modified_time,
                    created_time=entry.created_time,
                    is_deleted=False,
                    last_seen_at=datetime.now(timezone.utc),
                    search_vector=func.to_tsvector("english", search_text),
                )
                stmt = stmt.on_conflict_do_update(
                    constraint="uq_smb_file_tenant_path",
                    set_={
                        "filename": stmt.excluded.filename,
                        "ext": stmt.excluded.ext,
                        "mime_type": stmt.excluded.mime_type,
                        "snippet": stmt.excluded.snippet,
                        "owner": stmt.excluded.owner,
                        "size_bytes": stmt.excluded.size_bytes,
                        "modified_time": stmt.excluded.modified_time,
                        "created_time": stmt.excluded.created_time,
                        "is_deleted": False,
                        "last_seen_at": stmt.excluded.last_seen_at,
                        "search_vector": stmt.excluded.search_vector,
                        "share_id": stmt.excluded.share_id,
                        "agent_id": stmt.excluded.agent_id,
                    },
                )
                await db.execute(stmt)
                synced += 1
                if activates_row and entry.path not in activated_paths:
                    activated_paths.add(entry.path)
                    current_count += 1
            except Exception as exc:
                errors.append({"path": entry.path, "error": str(exc)})

        deleted = 0
        for path in sync_data.deletions:
            try:
                result = await db.execute(
                    update(SmbFileIndex)
                    .where(
                        SmbFileIndex.tenant_id == tenant_uuid,
                        SmbFileIndex.share_id == share_uuid,
                        SmbFileIndex.agent_id == agent_uuid,
                        SmbFileIndex.path == path,
                    )
                    .values(is_deleted=True)
                )
                deleted += result.rowcount
            except Exception as exc:
                errors.append({"path": path, "error": str(exc)})

        if synced or deleted:
            # Chat RAG results are cached by this tenant generation. Without
            # advancing it, newly indexed/changed/deleted share files can leave
            # stale matter answers in cache until TTL expiry.
            await advance_rag_corpus_revision(db, tenant_uuid)

        await db.flush()
        return SyncResponse(synced=synced, deleted=deleted, errors=errors)

    async def get_pending_tasks(
        self,
        db: AsyncSession,
        agent_id: str,
        redis=None,
        limit: int = 10,
        wait_seconds: int = 0,
    ) -> list[ContentFetchTask]:
        """Return pending tasks, optionally waiting briefly for new work.

        Tasks are stored in Redis under key ``smb_task_pending:<agent_id>:<task_id>``.
        Long polling keeps the connector responsive without opening inbound
        access to the customer network or hammering the API with empty polls.
        """
        if not redis:
            return []

        deadline = time.monotonic() + max(0, wait_seconds)
        while True:
            tasks: list[ContentFetchTask] = []
            # KEYS blocks Redis while it walks the entire keyspace. Agent
            # polling is continuous, so incrementally SCAN and stop as soon as
            # this poll has enough work.
            async for key in redis.scan_iter(
                match=f"smb_task_pending:{agent_id}:*", count=max(100, limit * 4)
            ):
                raw = await redis.get(key)
                if raw:
                    data = json.loads(raw if isinstance(raw, str) else raw.decode())
                    tasks.append(ContentFetchTask(**data))
                    if len(tasks) >= limit:
                        break
            if tasks or time.monotonic() >= deadline:
                return tasks
            await asyncio.sleep(min(0.5, max(0.0, deadline - time.monotonic())))

    async def submit_task_result(
        self,
        db: AsyncSession,
        agent_id: str,
        task_id: str,
        result: ContentFetchResult,
        redis=None,
        tenant_id: str | None = None,
    ) -> None:
        """Store a task result in Redis and apply operational side effects.

        Content fetches only need the Redis handoff. Verify/scan results also
        update the share (and its credential) so the admin console can show why
        a share is or is not reachable.
        """
        if redis is None:
            raise RuntimeError("SMB relay is temporarily unavailable")

        pending_key = f"smb_task_pending:{agent_id}:{task_id}"
        raw_meta = await redis.get(pending_key)
        if raw_meta is None:
            # A network timeout can hide a successful first submission from
            # the agent. Treat an exact retry as idempotent, while still
            # rejecting arbitrary result injection for unknown task ids.
            completed = None
            if tenant_id:
                completed = await redis.get(f"smb_task:{tenant_id}:{task_id}")
            if completed:
                try:
                    completed_payload = json.loads(
                        completed if isinstance(completed, str) else completed.decode()
                    )
                except (ValueError, UnicodeDecodeError):
                    completed_payload = {}
                if str(completed_payload.get("agent_id")) == str(agent_id) and str(
                    completed_payload.get("task_id")
                ) == str(task_id):
                    return
            # Do not allow an authenticated agent to inject arbitrary results
            # under an unknown/expired task id.  The pending key also binds
            # the result to the agent encoded in the Redis namespace.
            raise ValueError("Task not found, expired, or already completed")
        task_meta: dict = {}
        if raw_meta:
            try:
                task_meta = json.loads(
                    raw_meta if isinstance(raw_meta, str) else raw_meta.decode()
                )
            except (ValueError, UnicodeDecodeError):
                task_meta = {}

        task_tenant_id = str(task_meta.get("tenant_id") or tenant_id or "")
        if not task_tenant_id:
            raise ValueError("Task is missing its tenant binding")
        if tenant_id and task_tenant_id != str(tenant_id):
            raise ValueError("Task tenant mismatch")

        task_kind = task_meta.get("kind", "content_fetch")
        if task_kind == "local_search":
            if result.content or result.truncated:
                raise ValueError("Local search result cannot include file content")
            if result.detail is not None:
                try:
                    detail = LocalSearchResultDetail.model_validate(result.detail)
                except ValueError as exc:
                    raise ValueError("Invalid local search result detail") from exc
                result = result.model_copy(
                    update={"detail": detail.model_dump(mode="json")}
                )
            elif result.ok and not result.error:
                raise ValueError("Local search result detail is required")

        payload = json.dumps(
            {
                "task_id": task_id,
                "tenant_id": task_tenant_id,
                "agent_id": agent_id,
                "file_id": task_meta.get("file_id"),
                "user_id": task_meta.get("user_id"),
                "share_id": task_meta.get("share_id"),
                "content": result.content,
                "truncated": result.truncated,
                "error": result.error,
                "ok": result.ok and not result.error,
                "detail": result.detail,
                "kind": task_kind,
            }
        )
        access_log_id = task_meta.get("access_log_id")
        if access_log_id:
            update_result = await db.execute(
                update(SmbAccessLog)
                .where(
                    SmbAccessLog.id == _uuid(access_log_id),
                    SmbAccessLog.tenant_id == _uuid(task_tenant_id),
                    SmbAccessLog.agent_id == _uuid(agent_id),
                )
                .values(bytes_sent=len((result.content or "").encode("utf-8")))
            )
            if update_result.rowcount != 1:
                raise ValueError("Task access log binding is invalid")
        elif task_meta.get("kind", "content_fetch") == "content_fetch":
            raise ValueError("Content task is missing its access log binding")

        if tenant_id and task_meta.get("kind") in (
            *ADMIN_TASK_KINDS,
            AGENT_UPDATE_TASK_KIND,
        ):
            await self.record_task_outcome(db, tenant_id, task_meta, result)

        result_ttl = (
            REDIS_ADMIN_TASK_TTL
            if task_meta.get("kind") in (*ADMIN_TASK_KINDS, AGENT_UPDATE_TASK_KIND)
            else REDIS_TASK_TTL
        )
        await _commit_result_then_publish(
            db,
            redis,
            f"smb_task:{task_tenant_id}:{task_id}",
            pending_key,
            payload,
            result_ttl,
        )

    async def request_content_fetch(
        self,
        db: AsyncSession,
        tenant_id: str,
        user_id: str,
        file_id: str,
        conversation_id: str | None,
        reason: str,
        redis=None,
        matter_id: str | None = None,
    ) -> tuple[str, str]:
        """Create a content fetch task, log access, return (task_id, agent_id).

        This method owns the transaction boundary: the access log is committed
        before the Redis task becomes visible to an agent. Callers must not
        place unrelated uncommitted work on ``db`` before invoking it.
        """
        if redis is None:
            raise RuntimeError("SMB relay is temporarily unavailable")

        await set_tenant_context(db, tenant_id)

        tenant_uuid = _uuid(tenant_id)

        result = await db.execute(
            select(SmbFileIndex).where(
                SmbFileIndex.id == _uuid(file_id),
                SmbFileIndex.tenant_id == tenant_uuid,
                SmbFileIndex.is_deleted.is_(False),
            )
        )
        file_entry = result.scalar_one_or_none()
        if file_entry is None:
            raise ValueError("File not found")

        agent_id = str(file_entry.agent_id) if file_entry.agent_id else None
        share_id = str(file_entry.share_id) if file_entry.share_id else None
        if not agent_id or not share_id:
            raise ValueError("No agent assigned to file")

        assignment = (
            await db.execute(
                select(SmbAgent.id)
                .join(SmbShare, SmbShare.agent_id == SmbAgent.id)
                .where(
                    SmbAgent.id == _uuid(agent_id),
                    SmbAgent.tenant_id == tenant_uuid,
                    SmbAgent.status == "active",
                    SmbShare.id == _uuid(share_id),
                    SmbShare.tenant_id == tenant_uuid,
                    SmbShare.agent_id == _uuid(agent_id),
                    SmbShare.is_enabled.is_(True),
                )
            )
        ).scalar_one_or_none()
        if assignment is None:
            raise ValueError("File share agent is unavailable")

        identity_ticket = None
        if settings.FIRM_MEMORY_NATIVE_AUTHZ_ENABLED:
            if not settings.FIRM_MEMORY_ACL_COVERAGE_HEALTHY:
                raise ValueError("Matter file not found")
            if not matter_id:
                raise ValueError("Matter file not found")
            try:
                await require_matter_authorization(db, tenant_id, user_id, matter_id)
                identity = await resolve_native_identity(db, tenant_id, user_id)
            except NativeAuthorizationError as exc:
                raise ValueError("Matter file not found") from exc
            binding_row = (
                await db.execute(
                    select(MatterSmbShare, SmbShare)
                    .join(SmbShare, SmbShare.id == MatterSmbShare.share_id)
                    .where(
                        MatterSmbShare.tenant_id == tenant_uuid,
                        MatterSmbShare.matter_id == _uuid(matter_id),
                        MatterSmbShare.share_id == _uuid(share_id),
                        SmbShare.tenant_id == tenant_uuid,
                    )
                )
            ).one_or_none()
            if (
                binding_row is None
                or not _path_is_within_binding(
                    file_entry.path,
                    binding_row[1].share_path,
                    binding_row[0].folder_path,
                )
                or not settings.FIRM_MEMORY_IDENTITY_TICKET_PRIVATE_KEY
            ):
                raise ValueError("Matter file not found")
            identity_ticket = mint_search_identity_ticket(
                SearchIdentity(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    source_ids=(share_id,),
                    principal_sids=identity.principal_sids,
                    identity_version=identity.version,
                ),
                private_key=settings.FIRM_MEMORY_IDENTITY_TICKET_PRIVATE_KEY,
                audience=agent_id,
                filters={"matter_id": matter_id, "file_id": file_id},
                ttl_seconds=settings.FIRM_MEMORY_IDENTITY_TICKET_TTL_SECONDS,
            )

        task_id = secrets.token_urlsafe(16)

        access_log = SmbAccessLog(
            tenant_id=tenant_uuid,
            user_id=_uuid(user_id),
            agent_id=_uuid(agent_id),
            file_path=file_entry.path,
            conversation_id=_uuid(conversation_id),
            access_reason=reason,
            bytes_sent=None,
        )
        db.add(access_log)
        await db.flush()

        task = ContentFetchTask(
            task_id=task_id,
            file_path=file_entry.path,
            share_id=share_id,
            reason=reason,
            identity_ticket=identity_ticket,
        )

        pending_key = f"smb_task_pending:{agent_id}:{task_id}"
        task_payload = task.model_dump()
        task_payload.update(
            {
                "tenant_id": tenant_id,
                "file_id": file_id,
                "user_id": user_id,
                "matter_id": matter_id,
                "access_log_id": str(access_log.id),
            }
        )

        # Never expose a read task before its audit row is durable. Otherwise
        # a fast agent can return content while the log is still invisible—or
        # after its transaction ultimately rolls back.
        await _commit_audit_then_publish(
            db,
            redis,
            pending_key,
            task_payload,
        )

        return task_id, agent_id

    async def get_task_result(
        self,
        task_id: str,
        tenant_id: str,
        redis=None,
        *,
        file_id: str | None = None,
        share_id: str | None = None,
        kind: str | None = None,
        user_id: str | None = None,
    ) -> dict | None:
        """Return the full stored result payload for a task, or None if pending."""
        if not redis:
            return None
        raw = await redis.get(f"smb_task:{tenant_id}:{task_id}")
        if not raw:
            return None
        try:
            payload = json.loads(raw if isinstance(raw, str) else raw.decode())
        except (ValueError, UnicodeDecodeError):
            return None
        if str(payload.get("tenant_id")) != str(tenant_id):
            raise ValueError("Task tenant mismatch")
        if file_id and str(payload.get("file_id")) != str(file_id):
            raise ValueError("Task is not bound to this file")
        if share_id and str(payload.get("share_id")) != str(share_id):
            raise ValueError("Task is not bound to this share")
        if kind and payload.get("kind") != kind:
            raise ValueError("Task kind mismatch")
        if user_id and str(payload.get("user_id")) != str(user_id):
            raise ValueError("Task user mismatch")
        return payload

    async def get_content_result(
        self,
        task_id: str,
        tenant_id: str,
        file_id: str,
        redis=None,
    ) -> str | None:
        """Poll Redis for content fetch result. Return content if available, None if pending."""
        payload = await self.get_task_result(
            task_id,
            tenant_id,
            redis=redis,
            file_id=file_id,
            kind="content_fetch",
        )
        return payload.get("content") if payload else None

    async def poll_content_result(
        self,
        task_id: str,
        tenant_id: str,
        file_id: str,
        redis=None,
        timeout_seconds: int = 120,
    ) -> str | None:
        """Poll Redis for content fetch result with exponential backoff.

        Polls up to timeout_seconds, starting at 1s intervals and
        increasing to max 8s between polls.
        """
        if not redis:
            return None

        deadline = time.monotonic() + timeout_seconds
        delay = 1.0

        while time.monotonic() < deadline:
            await asyncio.sleep(delay)
            content = await self.get_content_result(
                task_id, tenant_id, file_id, redis=redis
            )
            if content is not None:
                return content
            delay = min(delay * 1.5, 8.0)

        return None

    async def search_files(
        self,
        db: AsyncSession,
        tenant_id: str,
        query: str,
        matter_id: str | None = None,
        file_extensions: list[str] | None = None,
        limit: int = 20,
    ) -> list[SmbSearchResult]:
        """Full-text search on smb_file_index using tsvector.

        If matter_id is given, scope to shares bound to that matter.
        Rank with ts_rank.
        """
        await set_tenant_context(db, tenant_id)

        # The SaaS metadata index has no authoritative native ACL snapshot.
        # Once native trimming is enabled, it must not release filenames or
        # snippets; callers use the identity-aware customer-node search path.
        if settings.FIRM_MEMORY_NATIVE_AUTHZ_ENABLED:
            return []
        tenant_uuid = _uuid(tenant_id)

        ts_query = func.plainto_tsquery("english", query)

        stmt = select(
            SmbFileIndex,
            func.ts_rank(SmbFileIndex.search_vector, ts_query).label("score"),
        ).where(
            SmbFileIndex.tenant_id == tenant_uuid,
            SmbFileIndex.is_deleted.is_(False),
            SmbFileIndex.search_vector.op("@@")(ts_query),
        )

        if matter_id:
            binding_rows = (
                await db.execute(
                    select(MatterSmbShare, SmbShare)
                    .join(SmbShare, SmbShare.id == MatterSmbShare.share_id)
                    .where(
                        MatterSmbShare.matter_id == _uuid(matter_id),
                        MatterSmbShare.tenant_id == tenant_uuid,
                        SmbShare.tenant_id == tenant_uuid,
                    )
                )
            ).all()
            if not binding_rows:
                return []

            # Keep each folder predicate paired with its own share. A global
            # ``share_id IN (...) AND path contains any folder`` lets a folder
            # from share A widen the scope of share B and substring matching
            # also admits siblings such as ``Client-10`` for ``Client-1``.
            binding_filters = []
            for binding, share in binding_rows:
                condition = SmbFileIndex.share_id == binding.share_id
                folder = _normalize_folder_path(binding.folder_path)
                if folder:
                    absolute = (
                        share.share_path.rstrip("\\/")
                        + "\\"
                        + folder.replace("/", "\\")
                    )
                    escaped = _escape_like(absolute)
                    condition = and_(
                        condition,
                        sa_or(
                            SmbFileIndex.path.ilike(escaped, escape="!"),
                            SmbFileIndex.path.ilike(escaped + "!\\%", escape="!"),
                        ),
                    )
                binding_filters.append(condition)
            stmt = stmt.where(sa_or(*binding_filters))

        if file_extensions:
            stmt = stmt.where(SmbFileIndex.ext.in_(file_extensions))

        stmt = stmt.order_by(func.ts_rank(SmbFileIndex.search_vector, ts_query).desc())
        stmt = stmt.limit(limit)

        rows = (await db.execute(stmt)).all()

        results: list[SmbSearchResult] = []
        for row in rows:
            file_entry = row[0]
            score = row[1]
            results.append(
                SmbSearchResult(
                    id=str(file_entry.id),
                    path=file_entry.path,
                    filename=file_entry.filename,
                    ext=file_entry.ext,
                    snippet=file_entry.snippet[:SMB_SNIPPET_MAX_CHARS]
                    if file_entry.snippet
                    else None,
                    owner=file_entry.owner,
                    size_bytes=file_entry.size_bytes,
                    modified_time=file_entry.modified_time,
                    created_time=file_entry.created_time,
                    score=float(score) if score is not None else None,
                )
            )
        return results

    async def get_matter_file(
        self,
        db: AsyncSession,
        tenant_id: str,
        user_id: str,
        matter_id: str,
        file_id: str,
        redis=None,
    ) -> FirmMemorySearchHit:
        """Resolve one opaque file id inside the same matter scope as search.

        This supports safe portal deep links. The browser receives a canonical
        UNC path only after the tenant, matter/share binding, active index row,
        and bound folder are rechecked; the id never becomes a raw file URL.
        """
        tenant_uuid = _uuid(tenant_id)
        matter_uuid = _uuid(matter_id)
        file_uuid = _uuid(file_id)
        if not tenant_uuid or not matter_uuid or not file_uuid:
            raise ValueError("Matter file not found")

        await set_tenant_context(db, tenant_id)
        try:
            await require_matter_authorization(db, tenant_id, user_id, matter_id)
        except NativeAuthorizationError as exc:
            raise ValueError("Matter file not found") from exc
        rows = (
            await db.execute(
                select(SmbFileIndex, SmbShare, MatterSmbShare)
                .join(
                    SmbShare,
                    and_(
                        SmbShare.id == SmbFileIndex.share_id,
                        SmbShare.tenant_id == tenant_uuid,
                    ),
                )
                .join(
                    MatterSmbShare,
                    and_(
                        MatterSmbShare.share_id == SmbShare.id,
                        MatterSmbShare.tenant_id == tenant_uuid,
                        MatterSmbShare.matter_id == matter_uuid,
                    ),
                )
                .where(
                    SmbFileIndex.id == file_uuid,
                    SmbFileIndex.tenant_id == tenant_uuid,
                    SmbFileIndex.is_deleted.is_(False),
                    SmbShare.is_enabled.is_(True),
                )
            )
        ).all()

        for file_entry, share, binding in rows:
            share_root = str(share.share_path or "").replace("/", "\\").rstrip("\\")
            folder = _normalize_folder_path(binding.folder_path)
            allowed_root = share_root + (f"\\{folder}" if folder else "")
            candidate = str(file_entry.path or "").replace("/", "\\").rstrip("\\")
            if (
                candidate.casefold() != allowed_root.casefold()
                and not candidate.casefold().startswith(allowed_root.casefold() + "\\")
            ):
                continue
            if settings.FIRM_MEMORY_NATIVE_AUTHZ_ENABLED:
                await self._revalidate_file_authorization(
                    db,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    matter_id=matter_id,
                    file_entry=file_entry,
                    agent_id=str(file_entry.agent_id or ""),
                    share_id=str(file_entry.share_id or ""),
                    redis=redis,
                )
            return FirmMemorySearchHit(
                id=str(file_entry.id),
                path=file_entry.path,
                filename=file_entry.filename,
                ext=file_entry.ext,
                snippet=(
                    file_entry.snippet[:SMB_SNIPPET_MAX_CHARS]
                    if file_entry.snippet
                    else None
                ),
                page_number=None,
                score=None,
                owner=file_entry.owner,
                size_bytes=file_entry.size_bytes,
                modified_time=file_entry.modified_time,
                created_time=file_entry.created_time,
                share_id=str(file_entry.share_id),
            )
        raise ValueError("Matter file not found")

    async def _revalidate_file_authorization(
        self,
        db: AsyncSession,
        *,
        tenant_id: str,
        user_id: str,
        matter_id: str,
        file_entry: SmbFileIndex,
        agent_id: str,
        share_id: str,
        redis,
    ) -> None:
        """Ask the customer node for a fresh fail-closed ACL decision."""
        if redis is None or not agent_id or not share_id:
            raise ValueError("Matter file not found")
        if not settings.FIRM_MEMORY_ACL_COVERAGE_HEALTHY:
            raise ValueError("Matter file not found")
        try:
            identity = await resolve_native_identity(db, tenant_id, user_id)
        except NativeAuthorizationError as exc:
            raise ValueError("Matter file not found") from exc
        if not settings.FIRM_MEMORY_IDENTITY_TICKET_PRIVATE_KEY:
            raise ValueError("Matter file not found")
        ticket = mint_search_identity_ticket(
            SearchIdentity(
                tenant_id=tenant_id,
                user_id=user_id,
                source_ids=(share_id,),
                principal_sids=identity.principal_sids,
                identity_version=identity.version,
            ),
            private_key=settings.FIRM_MEMORY_IDENTITY_TICKET_PRIVATE_KEY,
            audience=agent_id,
            filters={"matter_id": matter_id, "file_id": str(file_entry.id)},
            ttl_seconds=settings.FIRM_MEMORY_IDENTITY_TICKET_TTL_SECONDS,
        )
        task_id = secrets.token_urlsafe(16)
        task = ContentFetchTask(
            task_id=task_id,
            kind="authorize_file",
            file_path=file_entry.path,
            share_id=share_id,
            reason="preview_open_revalidation",
            identity_ticket=ticket,
        )
        access_log = SmbAccessLog(
            tenant_id=_uuid(tenant_id),
            user_id=_uuid(user_id),
            agent_id=_uuid(agent_id),
            file_path=file_entry.path,
            access_reason="authorization_revalidate",
        )
        db.add(access_log)
        await db.flush()
        pending_key = f"smb_task_pending:{agent_id}:{task_id}"
        await _commit_audit_then_publish(
            db,
            redis,
            pending_key,
            {
                **task.model_dump(),
                "tenant_id": tenant_id,
                "agent_id": agent_id,
                "matter_id": matter_id,
                "file_id": str(file_entry.id),
                "access_log_id": str(access_log.id),
            },
        )
        deadline = time.monotonic() + LOCAL_SEARCH_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            payload = await self.get_task_result(
                task_id,
                tenant_id,
                redis=redis,
                file_id=str(file_entry.id),
                kind="authorize_file",
            )
            if payload is not None:
                detail = (
                    payload.get("detail")
                    if isinstance(payload.get("detail"), dict)
                    else {}
                )
                if payload.get("ok") and detail.get("authorized") is True:
                    return
                raise ValueError("Matter file not found")
            await asyncio.sleep(LOCAL_SEARCH_POLL_SECONDS)
        try:
            await redis.delete(pending_key)
        except Exception:
            pass
        raise ValueError("Matter file not found")

    async def search_local_files(
        self,
        db: AsyncSession,
        tenant_id: str,
        user_id: str,
        matter_id: str,
        query: str,
        file_extensions: list[str] | None = None,
        limit: int = 20,
        correlation_id: str | None = None,
        redis=None,
        timeout_seconds: float = LOCAL_SEARCH_TIMEOUT_SECONDS,
    ) -> FirmMemorySearchResponse:
        """Run one local search while holding a short per-user Redis lease."""
        if redis is None:
            raise RuntimeError("SMB relay is temporarily unavailable")
        correlation_id = correlation_id or secrets.token_hex(12)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", correlation_id):
            raise ValueError("Invalid correlation id")
        try:
            timeout_seconds = max(0.1, min(float(timeout_seconds), 30.0))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("Invalid search timeout") from exc

        # One in-flight slot per user keeps repeated clicks or MCP retries from
        # piling expensive reads onto an old file server. The compare-and-delete
        # release in ``finally`` prevents failures from holding the slot.
        gate_key = f"firm_memory_search_gate:{tenant_id}:{user_id}"
        gate_acquired = await redis.set(
            gate_key,
            correlation_id,
            ex=max(5, math.ceil(timeout_seconds) + 5),
            nx=True,
        )
        if not gate_acquired:
            raise RuntimeError("A Firm Memory search is already in progress")
        try:
            return await self._search_local_files_once(
                db,
                tenant_id,
                user_id,
                matter_id,
                query,
                file_extensions,
                limit,
                correlation_id,
                redis,
                timeout_seconds,
            )
        finally:
            try:
                await redis.eval(
                    """
                    if redis.call('GET', KEYS[1]) == ARGV[1] then
                        return redis.call('DEL', KEYS[1])
                    end
                    return 0
                    """,
                    1,
                    gate_key,
                    correlation_id,
                )
            except Exception:
                # The TTL is the recovery path; do not mask the search outcome.
                logger.warning(
                    "Unable to release firm-memory search gate",
                    extra={"firm_memory_correlation_id": correlation_id},
                )

    async def _search_local_files_once(
        self,
        db: AsyncSession,
        tenant_id: str,
        user_id: str,
        matter_id: str,
        query: str,
        file_extensions: list[str] | None = None,
        limit: int = 20,
        correlation_id: str | None = None,
        redis=None,
        timeout_seconds: float = LOCAL_SEARCH_TIMEOUT_SECONDS,
    ) -> FirmMemorySearchResponse:
        """Fan out a bounded, matter-scoped full-text query to local agents.

        Agent paths and scores are advisory only. Every returned path is
        reconstructed from the registered share and matched to the current,
        non-deleted SaaS index before it can leave this method. The query is
        placed only in the short-lived relay task; it is never logged or
        written to a database audit row.
        """
        if redis is None:
            raise RuntimeError("SMB relay is temporarily unavailable")
        query = query.strip()
        if not query:
            raise ValueError("query must not be blank")
        tenant_uuid = _uuid(tenant_id)
        matter_uuid = _uuid(matter_id)
        if not tenant_uuid or not matter_uuid:
            raise ValueError("Invalid tenant or matter")
        correlation_id = correlation_id or secrets.token_hex(12)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", correlation_id):
            raise ValueError("Invalid correlation id")
        try:
            timeout_seconds = max(0.1, min(float(timeout_seconds), 30.0))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("Invalid search timeout") from exc

        started = time.monotonic()
        await set_tenant_context(db, tenant_id)
        try:
            await require_matter_authorization(db, tenant_id, user_id, matter_id)
        except NativeAuthorizationError as exc:
            raise ValueError(str(exc)) from exc
        native_identity = None
        if settings.FIRM_MEMORY_NATIVE_AUTHZ_ENABLED:
            if not settings.FIRM_MEMORY_ACL_COVERAGE_HEALTHY:
                raise RuntimeError("native ACL coverage is not healthy")
            try:
                native_identity = await resolve_native_identity(db, tenant_id, user_id)
            except NativeAuthorizationError as exc:
                raise RuntimeError(str(exc)) from exc
            if not settings.FIRM_MEMORY_IDENTITY_TICKET_PRIVATE_KEY:
                raise RuntimeError("native authorization signing is unavailable")

        # The paired binding/share predicates are deliberately kept together;
        # a folder from one share must never widen another share's scope.
        rows = (
            await db.execute(
                select(MatterSmbShare, SmbShare, SmbAgent)
                .join(SmbShare, SmbShare.id == MatterSmbShare.share_id)
                .join(SmbAgent, SmbAgent.id == SmbShare.agent_id)
                .where(
                    MatterSmbShare.matter_id == matter_uuid,
                    MatterSmbShare.tenant_id == tenant_uuid,
                    SmbShare.tenant_id == tenant_uuid,
                    SmbAgent.tenant_id == tenant_uuid,
                    SmbShare.is_enabled.is_(True),
                )
            )
        ).all()
        if not rows:
            raise ValueError("Matter not found or has no assigned SMB shares")

        grouped: dict[str, dict] = {}
        for binding, share, agent in rows:
            if agent.status != "active":
                continue
            key = str(agent.id)
            grouped.setdefault(key, {"agent": agent, "scopes": []})["scopes"].append(
                {
                    "share_id": str(share.id),
                    "folder_path": _normalize_folder_path(binding.folder_path),
                }
            )
        if not grouped:
            raise ValueError("No active SMB agent is available for this matter")

        tasks: dict[str, str] = {}
        statuses = [
            FirmMemoryAgentStatus(agent_id=agent_id, status="queued")
            for agent_id in grouped
        ]
        for agent_id, group in grouped.items():
            task_id = secrets.token_urlsafe(16)
            identity_ticket = None
            if native_identity is not None:
                identity_ticket = mint_search_identity_ticket(
                    SearchIdentity(
                        tenant_id=tenant_id,
                        user_id=user_id,
                        source_ids=tuple(
                            scope["share_id"] for scope in group["scopes"]
                        ),
                        principal_sids=native_identity.principal_sids,
                        identity_version=native_identity.version,
                    ),
                    private_key=settings.FIRM_MEMORY_IDENTITY_TICKET_PRIVATE_KEY,
                    audience=agent_id,
                    filters={
                        "matter_id": matter_id,
                        "file_extensions": file_extensions or [],
                    },
                    ttl_seconds=settings.FIRM_MEMORY_IDENTITY_TICKET_TTL_SECONDS,
                )
            task = ContentFetchTask(
                task_id=task_id,
                kind="local_search",
                reason="firm_memory_search",
                query=query,
                scopes=group["scopes"],
                file_extensions=file_extensions,
                limit=limit,
                correlation_id=correlation_id,
                identity_ticket=identity_ticket,
            )
            await redis.set(
                f"smb_task_pending:{agent_id}:{task_id}",
                json.dumps(
                    {
                        **task.model_dump(),
                        "tenant_id": tenant_id,
                        "agent_id": agent_id,
                        "matter_id": matter_id,
                    }
                ),
                ex=REDIS_TASK_TTL,
            )
            tasks[agent_id] = task_id

        async def wait_one(agent_id: str, task_id: str):
            deadline = time.monotonic() + timeout_seconds
            while time.monotonic() < deadline:
                payload = await self.get_task_result(
                    task_id, tenant_id, redis=redis, kind="local_search"
                )
                if payload is not None:
                    return payload
                await asyncio.sleep(
                    min(LOCAL_SEARCH_POLL_SECONDS, max(0, deadline - time.monotonic()))
                )
            return None

        results = await asyncio.gather(
            *(wait_one(agent_id, task_id) for agent_id, task_id in tasks.items()),
            return_exceptions=True,
        )
        hits_by_id: dict[str, FirmMemorySearchHit] = {}
        errors: list[str] = []

        def safe_nonnegative_int(value) -> int:
            try:
                return max(0, int(value or 0))
            except (TypeError, ValueError, OverflowError):
                return 0

        def safe_score(value) -> float:
            try:
                score = float(value or 0.0)
            except (TypeError, ValueError, OverflowError):
                return 0.0
            return (
                max(-1_000_000.0, min(1_000_000.0, score))
                if math.isfinite(score)
                else 0.0
            )

        def safe_page_number(value) -> int | None:
            try:
                page = int(value)
            except (TypeError, ValueError, OverflowError):
                return None
            return page if 1 <= page <= 10_000_000 else None

        async def discard_pending(agent_id: str) -> None:
            try:
                await redis.delete(f"smb_task_pending:{agent_id}:{tasks[agent_id]}")
            except Exception:
                logger.warning(
                    "Unable to discard expired firm-memory search task",
                    extra={
                        "firm_memory_correlation_id": correlation_id,
                        "firm_memory_agent_id": agent_id,
                    },
                )

        for status, (agent_id, group), payload in zip(
            statuses, grouped.items(), results
        ):
            if isinstance(payload, Exception):
                status.status = "failed"
                errors.append("agent_search_failed")
                await discard_pending(agent_id)
                continue
            if payload is None:
                status.status = "timeout"
                errors.append("agent_search_timeout")
                await discard_pending(agent_id)
                continue
            detail = payload.get("detail")
            if not isinstance(detail, dict):
                detail = {}
            status.status = "ready" if payload.get("ok", True) else "failed"
            status.index_state = str(detail.get("index_state") or "unknown")[:40]
            status.indexed_files = safe_nonnegative_int(detail.get("indexed_files"))
            status.pending_files = safe_nonnegative_int(detail.get("pending_files"))
            status.duration_ms = safe_nonnegative_int(detail.get("duration_ms"))
            if status.status != "ready":
                errors.append("agent_search_failed")
                continue
            if detail.get("schema_version") != 1:
                errors.append("agent_search_schema_mismatch")
                continue
            if detail.get("correlation_id") != correlation_id:
                errors.append("agent_search_correlation_mismatch")
                continue
            allowed_scopes = group["scopes"]
            share_ids = {scope["share_id"] for scope in allowed_scopes}
            candidate_hits = (
                detail.get("hits") if isinstance(detail.get("hits"), list) else []
            )
            candidates: dict[tuple[str, str], tuple[dict, float]] = {}
            for raw in candidate_hits[: limit * 3]:
                if (
                    not isinstance(raw, dict)
                    or str(raw.get("share_id")) not in share_ids
                ):
                    continue
                share_id = str(raw["share_id"])
                relative = (
                    str(raw.get("relative_path") or "").replace("/", "\\").strip("\\")
                )
                if not relative or ".." in relative.split("\\"):
                    continue
                scope = next(
                    item for item in allowed_scopes if item["share_id"] == share_id
                )
                folder = (scope.get("folder_path") or "").replace("/", "\\").strip("\\")
                if (
                    folder
                    and relative.casefold() != folder.casefold()
                    and not relative.casefold().startswith(folder.casefold() + "\\")
                ):
                    continue
                share = next(
                    (row[1] for row in rows if str(row[1].id) == share_id), None
                )
                if share is None:
                    continue
                canonical = share.share_path.rstrip("\\/") + "\\" + relative
                key = (share_id, canonical.casefold())
                score = safe_score(raw.get("score"))
                previous = candidates.get(key)
                if previous is None or score > previous[1]:
                    candidates[key] = (raw, score)

            if not candidates:
                continue
            file_rows = (
                (
                    await db.execute(
                        select(SmbFileIndex).where(
                            SmbFileIndex.tenant_id == tenant_uuid,
                            SmbFileIndex.agent_id == _uuid(agent_id),
                            SmbFileIndex.share_id.in_(
                                [_uuid(share_id) for share_id in share_ids]
                            ),
                            SmbFileIndex.is_deleted.is_(False),
                            func.lower(SmbFileIndex.path).in_(
                                [key[1] for key in candidates]
                            ),
                        )
                    )
                )
                .scalars()
                .all()
            )
            for file_entry in file_rows:
                key = (str(file_entry.share_id), file_entry.path.casefold())
                candidate = candidates.get(key)
                if candidate is None:
                    continue
                raw, score = candidate
                hit = FirmMemorySearchHit(
                    id=str(file_entry.id),
                    path=file_entry.path,
                    filename=file_entry.filename,
                    ext=file_entry.ext,
                    snippet=(
                        str(raw.get("snippet") or "")[:SMB_SNIPPET_MAX_CHARS] or None
                    ),
                    page_number=safe_page_number(raw.get("page_number")),
                    score=score,
                    owner=file_entry.owner,
                    size_bytes=file_entry.size_bytes,
                    modified_time=file_entry.modified_time,
                    created_time=file_entry.created_time,
                    share_id=str(file_entry.share_id),
                )
                previous = hits_by_id.get(str(file_entry.id))
                if previous is None or (hit.score or 0) > (previous.score or 0):
                    hits_by_id[str(file_entry.id)] = hit

        ranked = sorted(
            hits_by_id.values(),
            key=lambda hit: (
                -(hit.score or 0),
                hit.filename.casefold(),
                hit.id,
            ),
        )[:limit]
        duration_ms = int((time.monotonic() - started) * 1000)
        partial = bool(errors)
        logger.info(
            "Firm-memory local search completed",
            extra={
                "firm_memory_correlation_id": correlation_id,
                "firm_memory_tenant_id": tenant_id,
                "firm_memory_matter_id": matter_id,
                "firm_memory_user_id": user_id,
                "firm_memory_result_count": len(ranked),
                "firm_memory_agent_count": len(statuses),
                "firm_memory_duration_ms": duration_ms,
                "firm_memory_partial": partial,
                "firm_memory_indexed_files": sum(
                    status.indexed_files for status in statuses
                ),
                "firm_memory_pending_files": sum(
                    status.pending_files for status in statuses
                ),
            },
        )
        return FirmMemorySearchResponse(
            correlation_id=correlation_id,
            hits=ranked,
            duration_ms=duration_ms,
            agent_statuses=statuses,
            partial=partial,
            degraded=partial or not ranked,
            errors=sorted(set(errors)),
        )

    async def create_share(
        self,
        db: AsyncSession,
        agent_id: str,
        tenant_id: str,
        data: ShareCreate,
        user_id: str | None = None,
    ) -> SmbShare:
        """Create a new SMB share configuration for an agent.

        The caller may either reference an existing stored credential or send a
        new one inline, which is created (encrypted) and attached in the same
        transaction so the admin never has to pre-create credentials just to
        add a share.
        """
        await set_tenant_context(db, tenant_id)

        # A short-lived pairing reservation must never own a share. This keeps
        # lifecycle cleanup from cascading tenant configuration and also
        # prevents credentials from being routed to an unregistered device.
        await smb_credential_service.require_registered_agent(db, tenant_id, agent_id)

        credential_id = await self._resolve_share_credential(
            db, tenant_id, agent_id, data.credential_id, data.credential, user_id
        )

        share = SmbShare(
            agent_id=_uuid(agent_id),
            tenant_id=_uuid(tenant_id),
            credential_id=credential_id,
            share_path=data.share_path,
            display_name=data.display_name,
            file_extensions=_normalize_extensions(data.file_extensions),
            exclude_patterns=data.exclude_patterns or None,
            max_depth=data.max_depth if data.max_depth is not None else 10,
            scan_schedule=data.scan_schedule or "0 */6 * * *",
            is_enabled=True if data.is_enabled is None else data.is_enabled,
        )
        db.add(share)
        await db.flush()
        return share

    async def update_share(
        self,
        db: AsyncSession,
        share_id: str,
        tenant_id: str,
        data: ShareUpdate,
    ) -> SmbShare:
        """Update an existing SMB share configuration."""
        await set_tenant_context(db, tenant_id)

        result = await db.execute(
            select(SmbShare).where(
                SmbShare.id == _uuid(share_id),
                SmbShare.tenant_id == _uuid(tenant_id),
            )
        )
        share = result.scalar_one_or_none()
        if share is None:
            raise ValueError("Share not found")

        target_agent_id = str(data.agent_id or share.agent_id)
        target_path = data.share_path or share.share_path
        await smb_credential_service.require_registered_agent(
            db, tenant_id, target_agent_id
        )
        duplicate = await db.execute(
            select(SmbShare.id).where(
                SmbShare.tenant_id == _uuid(tenant_id),
                SmbShare.agent_id == _uuid(target_agent_id),
                SmbShare.share_path == target_path,
                SmbShare.id != _uuid(share_id),
            )
        )
        if duplicate.scalar_one_or_none() is not None:
            raise SmbShareConflictError(
                "A share with this path already exists on the selected agent"
            )

        assignment_changed = (
            str(share.agent_id) != target_agent_id or share.share_path != target_path
        )
        if (
            assignment_changed
            and data.credential is None
            and data.credential_id is None
            and share.credential_id is not None
        ):
            # Validate before mutating the ORM row so a pinned credential
            # cannot leave a partially moved share in the transaction.
            await self._resolve_share_credential(
                db, tenant_id, target_agent_id, str(share.credential_id)
            )
        if data.share_path is not None:
            share.share_path = target_path
        if data.agent_id is not None:
            share.agent_id = _uuid(target_agent_id)

        if data.display_name is not None:
            share.display_name = data.display_name
        if data.file_extensions is not None:
            share.file_extensions = _normalize_extensions(data.file_extensions)
        if data.exclude_patterns is not None:
            share.exclude_patterns = data.exclude_patterns or None
        if data.max_depth is not None:
            share.max_depth = data.max_depth
        if data.scan_schedule is not None:
            share.scan_schedule = data.scan_schedule
        if data.is_enabled is not None:
            share.is_enabled = data.is_enabled

        if data.credential is not None or data.credential_id is not None:
            if data.credential is None and data.credential_id == "":
                # Explicit detach: fall back to the agent's own identity.
                share.credential_id = None
            else:
                share.credential_id = await self._resolve_share_credential(
                    db,
                    tenant_id,
                    str(share.agent_id),
                    data.credential_id,
                    data.credential,
                )
            # A credential change invalidates the last connection test.
            share.last_verified_at = None
            share.last_verify_status = None
            share.last_verify_error = None

        if assignment_changed:
            # A different path/agent has never been verified or scanned by
            # this share row. Hide its old corpus entries immediately rather
            # than leaving stale matter/context results until another scan.
            await db.execute(
                update(SmbFileIndex)
                .where(
                    SmbFileIndex.tenant_id == _uuid(tenant_id),
                    SmbFileIndex.share_id == _uuid(share_id),
                )
                .values(is_deleted=True)
            )
            await advance_rag_corpus_revision(db, _uuid(tenant_id))

            # Force the portal/agent to establish fresh operational state.
            share.last_scan_at = None
            share.last_scan_status = None
            share.last_scan_file_count = None
            share.last_scan_error = None
            share.last_verified_at = None
            share.last_verify_status = None
            share.last_verify_error = None
        await db.flush()
        return share

    async def delete_share(
        self,
        db: AsyncSession,
        share_id: str,
        tenant_id: str,
    ) -> None:
        """Delete an SMB share and its file index entries."""
        await set_tenant_context(db, tenant_id)

        share_uuid = _uuid(share_id)
        tenant_uuid = _uuid(tenant_id)
        await db.execute(
            delete(SmbFileIndex).where(
                SmbFileIndex.share_id == share_uuid,
                SmbFileIndex.tenant_id == tenant_uuid,
            )
        )
        await db.execute(
            delete(SmbShare).where(
                SmbShare.id == share_uuid,
                SmbShare.tenant_id == tenant_uuid,
            )
        )
        await db.flush()

    async def list_shares(
        self,
        db: AsyncSession,
        agent_id: str,
        tenant_id: str,
    ) -> list[SmbShare]:
        """List all shares for an agent."""
        await set_tenant_context(db, tenant_id)

        result = await db.execute(
            select(SmbShare).where(
                SmbShare.agent_id == _uuid(agent_id),
                SmbShare.tenant_id == _uuid(tenant_id),
            )
        )
        return list(result.scalars().all())

    # ── Credentials and agent-facing share config ───────────────────────────

    async def _resolve_share_credential(
        self,
        db: AsyncSession,
        tenant_id: str,
        agent_id: str,
        credential_id: str | None,
        inline_credential=None,
        user_id: str | None = None,
    ):
        """Return the credential UUID a share should use, creating it if inline."""
        if inline_credential is not None:
            credential = await smb_credential_service.create_credential(
                db, tenant_id, inline_credential, user_id
            )
            return credential.id

        if not credential_id:
            return None

        credential = await smb_credential_service.get_credential(
            db, credential_id, tenant_id
        )
        if credential.agent_id and str(credential.agent_id) != str(agent_id):
            raise SmbCredentialError(
                f"Credential '{credential.name}' is pinned to a different agent"
            )
        return credential.id

    async def share_info(
        self,
        db: AsyncSession,
        share: SmbShare,
        credential_names: dict[str, str] | None = None,
        agent_names: dict[str, str] | None = None,
    ) -> ShareInfo:
        """Build the admin view of a share, resolving display names."""
        info = ShareInfo.model_validate(share)
        credential_name = None
        if share.credential_id:
            key = str(share.credential_id)
            if credential_names is not None:
                credential_name = credential_names.get(key)
            else:
                result = await db.execute(
                    select(SmbCredential.name).where(
                        SmbCredential.id == share.credential_id
                    )
                )
                credential_name = result.scalar_one_or_none()
        agent_name = None
        if share.agent_id:
            key = str(share.agent_id)
            if agent_names is not None:
                agent_name = agent_names.get(key)
            else:
                result = await db.execute(
                    select(SmbAgent.agent_name).where(SmbAgent.id == share.agent_id)
                )
                agent_name = result.scalar_one_or_none()
        return info.model_copy(
            update={"credential_name": credential_name, "agent_name": agent_name}
        )

    async def name_maps(
        self, db: AsyncSession, tenant_id: str
    ) -> tuple[dict[str, str], dict[str, str]]:
        """Return ``(credential_names, agent_names)`` for a tenant."""
        tid = _uuid(tenant_id)
        cred_rows = await db.execute(
            select(SmbCredential.id, SmbCredential.name).where(
                SmbCredential.tenant_id == tid
            )
        )
        agent_rows = await db.execute(
            select(SmbAgent.id, SmbAgent.agent_name).where(SmbAgent.tenant_id == tid)
        )
        return (
            {str(r[0]): r[1] for r in cred_rows.all()},
            {str(r[0]): r[1] for r in agent_rows.all()},
        )

    async def list_agent_shares(
        self,
        db: AsyncSession,
        agent_id: str,
        tenant_id: str,
    ) -> list[AgentShareInfo]:
        """Share config for an authenticated agent, including its credentials.

        This is the only response that carries a decrypted secret, and it only
        reaches the agent that owns the share over its API-key-authenticated,
        TLS-protected channel.
        """
        shares = await self.list_shares(db, agent_id, tenant_id)

        payload: list[AgentShareInfo] = []
        for share in shares:
            if not share.is_enabled:
                continue
            server, share_name, root = _parse_unc(share.share_path)
            secret = None
            if share.credential_id:
                try:
                    secret = await smb_credential_service.resolve_secret(
                        db, share.credential_id, tenant_id, agent_id
                    )
                except SmbCredentialError:
                    secret = None
            payload.append(
                AgentShareInfo(
                    share_id=str(share.id),
                    share_path=share.share_path,
                    server=server,
                    share=share_name,
                    root_path=root,
                    display_name=share.display_name,
                    file_extensions=share.file_extensions,
                    exclude_patterns=share.exclude_patterns,
                    max_depth=share.max_depth,
                    scan_schedule=share.scan_schedule,
                    is_enabled=share.is_enabled,
                    credential=(AgentShareCredential(**secret) if secret else None),
                )
            )
        return payload

    async def record_scan_status(
        self,
        db: AsyncSession,
        agent_id: str,
        tenant_id: str,
        share_id: str,
        status: ShareScanStatus,
    ) -> SmbShare:
        """Persist a scan outcome reported by the agent."""
        await set_tenant_context(db, tenant_id)

        result = await db.execute(
            select(SmbShare).where(
                SmbShare.id == _uuid(share_id),
                SmbShare.tenant_id == _uuid(tenant_id),
                SmbShare.agent_id == _uuid(agent_id),
            )
        )
        share = result.scalar_one_or_none()
        if share is None:
            raise ValueError("Share not found")

        share.last_scan_status = status.status
        share.last_scan_at = status.finished_at or datetime.now(timezone.utc)
        if status.file_count is not None:
            share.last_scan_file_count = status.file_count
        share.last_scan_error = status.error[:2000] if status.error else None
        if status.status in ("success", "completed"):
            # A completed scan is also proof the credential still works.
            share.last_verified_at = share.last_scan_at
            share.last_verify_status = "ok"
            share.last_verify_error = None
            await smb_credential_service.record_verification(
                db,
                str(share.credential_id) if share.credential_id else None,
                tenant_id,
                True,
            )
        await db.flush()
        return share

    # ── Admin-triggered agent tasks ─────────────────────────────────────────

    async def enqueue_share_task(
        self,
        db: AsyncSession,
        tenant_id: str,
        share_id: str,
        kind: str,
        redis=None,
    ) -> tuple[str, str]:
        """Queue a verify/scan task for the agent owning a share.

        Returns ``(task_id, agent_id)``. Raises ``ValueError`` when the share is
        unknown, its agent is not active, or the queue is unavailable.
        """
        if kind not in ADMIN_TASK_KINDS:
            raise ValueError(f"Unsupported task kind: {kind}")

        await set_tenant_context(db, tenant_id)

        result = await db.execute(
            select(SmbShare).where(
                SmbShare.id == _uuid(share_id),
                SmbShare.tenant_id == _uuid(tenant_id),
            )
        )
        share = result.scalar_one_or_none()
        if share is None:
            raise ValueError("Share not found")

        agent_result = await db.execute(
            select(SmbAgent).where(SmbAgent.id == share.agent_id)
        )
        agent = agent_result.scalar_one_or_none()
        if agent is None:
            raise ValueError("Share has no agent")
        if agent.status != "active":
            raise ValueError(f"Agent is {agent.status}")

        if not redis:
            raise ValueError("Task queue unavailable; cannot reach the agent")

        task_id = secrets.token_urlsafe(16)
        task = ContentFetchTask(
            task_id=task_id,
            kind=kind,
            share_id=str(share.id),
            share_path=share.share_path,
            reason="admin_request",
        )
        await redis.set(
            f"smb_task_pending:{share.agent_id}:{task_id}",
            task.model_dump_json(),
            ex=REDIS_ADMIN_TASK_TTL,
        )

        if kind == "verify_share":
            share.last_verify_status = "pending"
            share.last_verify_error = None
        else:
            share.last_scan_status = "queued"
        await db.flush()

        return task_id, str(share.agent_id)

    async def record_task_outcome(
        self,
        db: AsyncSession,
        tenant_id: str,
        task_meta: dict,
        result: ContentFetchResult,
    ) -> None:
        """Apply a verify/scan task result to the share it belongs to."""
        share_id = task_meta.get("share_id")
        kind = task_meta.get("kind", "content_fetch")
        if kind == AGENT_UPDATE_TASK_KIND:
            agent_id = task_meta.get("agent_id")
            if not agent_id:
                return
            await set_tenant_context(db, tenant_id)
            agent_result = await db.execute(
                select(SmbAgent).where(
                    SmbAgent.id == _uuid(agent_id),
                    SmbAgent.tenant_id == _uuid(tenant_id),
                )
            )
            agent = agent_result.scalar_one_or_none()
            if agent and agent.update_task_id == task_meta.get("task_id"):
                agent.update_status = (
                    "in_progress" if result.ok and not result.error else "failed"
                )
                if agent.update_status == "failed":
                    agent.update_error = (
                        result.error or "The agent rejected the update request"
                    )[:2000]
                await db.flush()
            return
        if not share_id or kind not in ADMIN_TASK_KINDS:
            return

        await set_tenant_context(db, tenant_id)
        share_result = await db.execute(
            select(SmbShare).where(
                SmbShare.id == _uuid(share_id),
                SmbShare.tenant_id == _uuid(tenant_id),
            )
        )
        share = share_result.scalar_one_or_none()
        if share is None:
            return

        ok = result.ok and not result.error
        now = datetime.now(timezone.utc)
        if kind == "verify_share":
            share.last_verified_at = now
            share.last_verify_status = "ok" if ok else "failed"
            share.last_verify_error = None if ok else (result.error or "")[:2000]
            await smb_credential_service.record_verification(
                db,
                str(share.credential_id) if share.credential_id else None,
                tenant_id,
                ok,
                result.error,
            )
        elif not ok:
            share.last_scan_status = "failed"
            share.last_scan_at = now
            share.last_scan_error = (result.error or "")[:2000]
        await db.flush()

    async def create_matter_binding(
        self,
        db: AsyncSession,
        matter_id: str,
        tenant_id: str,
        data: MatterSmbShareCreate,
    ) -> MatterSmbShare:
        """Bind an SMB share/folder to a matter and persist path metadata."""
        await set_tenant_context(db, tenant_id)

        tenant_uuid = _uuid(tenant_id)
        matter_uuid = _uuid(matter_id)
        share_uuid = _uuid(data.share_id)

        matter = (
            await db.execute(
                select(Matter).where(
                    Matter.id == matter_uuid,
                    Matter.tenant_id == tenant_uuid,
                )
            )
        ).scalar_one_or_none()
        if matter is None:
            raise ValueError("Matter not found")

        share = (
            await db.execute(
                select(SmbShare).where(
                    SmbShare.id == share_uuid,
                    SmbShare.tenant_id == tenant_uuid,
                )
            )
        ).scalar_one_or_none()
        if share is None:
            raise ValueError("Share not found")

        folder_path = _normalize_folder_path(
            data.folder_path
            if data.folder_path is not None
            else matter_relative_path(matter.slug)
        )
        display_label = data.display_label or matter.matter_name

        binding = MatterSmbShare(
            tenant_id=tenant_uuid,
            matter_id=matter_uuid,
            share_id=share_uuid,
            folder_path=folder_path,
            display_label=display_label,
            auto_scan=data.auto_scan,
        )
        db.add(binding)
        await db.flush()

        smb_folders = dict(matter.smb_folders or {})
        smb_folders[str(binding.id)] = {
            "share_id": str(share.id),
            "share_path": share.share_path,
            "folder_path": folder_path,
            "display_label": display_label,
            "path": folder_path,
            "subfolder_names": MATTER_SUBFOLDERS.copy(),
            "subfolder_paths": {
                sub: f"{folder_path.rstrip('/')}/{sub}" if folder_path else sub
                for sub in MATTER_SUBFOLDERS
            },
            "auto_scan": data.auto_scan,
        }
        matter.smb_folders = smb_folders
        await db.flush()
        return binding

    async def list_matter_bindings(
        self,
        db: AsyncSession,
        matter_id: str,
        tenant_id: str,
    ) -> list[MatterSmbShare]:
        """List all SMB share bindings for a matter."""
        await set_tenant_context(db, tenant_id)

        result = await db.execute(
            select(MatterSmbShare).where(
                MatterSmbShare.matter_id == _uuid(matter_id),
                MatterSmbShare.tenant_id == _uuid(tenant_id),
            )
        )
        return list(result.scalars().all())

    async def delete_matter_binding(
        self,
        db: AsyncSession,
        binding_id: str,
        tenant_id: str,
    ) -> None:
        """Remove an SMB share binding from a matter."""
        await set_tenant_context(db, tenant_id)

        binding_uuid = _uuid(binding_id)
        binding = (
            await db.execute(
                select(MatterSmbShare).where(
                    MatterSmbShare.id == binding_uuid,
                    MatterSmbShare.tenant_id == _uuid(tenant_id),
                )
            )
        ).scalar_one_or_none()
        if binding:
            matter = (
                await db.execute(
                    select(Matter).where(
                        Matter.id == binding.matter_id,
                        Matter.tenant_id == _uuid(tenant_id),
                    )
                )
            ).scalar_one_or_none()
            if matter and matter.smb_folders:
                smb_folders = dict(matter.smb_folders)
                smb_folders.pop(str(binding.id), None)
                matter.smb_folders = smb_folders or None

        await db.execute(
            delete(MatterSmbShare).where(
                MatterSmbShare.id == binding_uuid,
                MatterSmbShare.tenant_id == _uuid(tenant_id),
            )
        )
        await db.flush()

    async def get_admin_stats(
        self,
        db: AsyncSession,
        tenant_id: str,
    ) -> dict:
        """Return admin dashboard stats for SMB feature."""
        await set_tenant_context(db, tenant_id)
        tid = _uuid(tenant_id)

        # Pending reservations and unregistered revoked tombstones are not
        # operational agents. Registered revoked rows remain auditable but are
        # intentionally excluded from this dashboard count as well.
        agent_count = (
            await db.execute(
                select(func.count(SmbAgent.id)).where(
                    SmbAgent.tenant_id == tid,
                    SmbAgent.status.in_(("active", "paused")),
                    SmbAgent.api_key_hash != "pending",
                )
            )
        ).scalar_one()

        active_agents = (
            await db.execute(
                select(func.count(SmbAgent.id)).where(
                    SmbAgent.tenant_id == tid,
                    SmbAgent.status == "active",
                    SmbAgent.api_key_hash != "pending",
                )
            )
        ).scalar_one()

        share_count = (
            await db.execute(
                select(func.count(SmbShare.id)).where(SmbShare.tenant_id == tid)
            )
        ).scalar_one()

        file_count = (
            await db.execute(
                select(func.count(SmbFileIndex.id)).where(
                    SmbFileIndex.tenant_id == tid,
                    SmbFileIndex.is_deleted.is_(False),
                )
            )
        ).scalar_one()

        total_size = (
            await db.execute(
                select(func.coalesce(func.sum(SmbFileIndex.size_bytes), 0)).where(
                    SmbFileIndex.tenant_id == tid,
                    SmbFileIndex.is_deleted.is_(False),
                )
            )
        ).scalar_one()

        recent_fetches = (
            await db.execute(
                select(func.count(SmbAccessLog.id)).where(
                    SmbAccessLog.tenant_id == tid,
                    SmbAccessLog.accessed_at
                    > datetime.now(timezone.utc) - timedelta(hours=24),
                )
            )
        ).scalar_one()

        credential_count = (
            await db.execute(
                select(func.count(SmbCredential.id)).where(
                    SmbCredential.tenant_id == tid
                )
            )
        ).scalar_one()

        # Shares an admin should look at: last scan or connection test failed.
        shares_failing = (
            await db.execute(
                select(func.count(SmbShare.id)).where(
                    SmbShare.tenant_id == tid,
                    sa_or(
                        SmbShare.last_scan_status.in_(["failed", "error"]),
                        SmbShare.last_verify_status == "failed",
                    ),
                )
            )
        ).scalar_one()

        shares_without_credential = (
            await db.execute(
                select(func.count(SmbShare.id)).where(
                    SmbShare.tenant_id == tid,
                    SmbShare.credential_id.is_(None),
                )
            )
        ).scalar_one()

        last_agent_heartbeat = (
            await db.execute(
                select(func.max(SmbAgent.last_heartbeat)).where(
                    SmbAgent.tenant_id == tid,
                    SmbAgent.status.in_(("active", "paused")),
                    SmbAgent.api_key_hash != "pending",
                )
            )
        ).scalar_one()

        # Share scan completion is a better operational signal than the newest
        # changed file: a successful no-change scan must still move this value.
        last_file_sync = (
            await db.execute(
                select(func.max(SmbShare.last_scan_at)).where(SmbShare.tenant_id == tid)
            )
        ).scalar_one()

        return {
            "agent_count": agent_count,
            "active_agents": active_agents,
            "share_count": share_count,
            "file_count": file_count,
            "total_size_bytes": int(total_size),
            "recent_fetches_24h": recent_fetches,
            "credential_count": credential_count,
            "shares_failing": shares_failing,
            "shares_without_credential": shares_without_credential,
            "last_agent_heartbeat": last_agent_heartbeat,
            "last_file_sync": last_file_sync,
        }

    async def get_access_log(
        self,
        db: AsyncSession,
        tenant_id: str,
        limit: int = 100,
    ) -> list[SmbAccessLog]:
        """Return recent access log entries for audit."""
        await set_tenant_context(db, tenant_id)

        result = await db.execute(
            select(SmbAccessLog)
            .where(SmbAccessLog.tenant_id == _uuid(tenant_id))
            .order_by(SmbAccessLog.accessed_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    @staticmethod
    async def build_smb_context(
        results: list[SmbSearchResult],
        include_snippets: bool = True,
    ) -> str:
        """Format SMB file search results into an LLM context string."""
        if not results:
            return ""

        parts = []
        for i, hit in enumerate(results, start=1):
            header_parts = [f"[S{i}] On-prem: {hit.filename}"]
            if hit.ext:
                header_parts.append(f" ({hit.ext})")
            header = "".join(header_parts)
            if hit.owner:
                header += f"\n    Owner: {hit.owner}"
            if hit.size_bytes:
                header += f"\n    Size: {hit.size_bytes:,} bytes"
            if hit.modified_time:
                header += f"\n    Modified: {hit.modified_time.isoformat()}"
            if hit.path:
                header += f"\n    Path: {hit.path}"

            snippet_text = ""
            if include_snippets and hit.snippet:
                snippet_text = f"\nSnippet:\n{hit.snippet}"

            parts.append(f"{header}{snippet_text}\n" + "-" * 60)

        return "\n\n".join(parts)


smb_service = SmbService()
