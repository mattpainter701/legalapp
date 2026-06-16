"""SMB file share service — agent pairing, sync, search, content fetch."""

import asyncio
import hashlib
import json
import logging
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import set_tenant_context
from app.models.matter_smb_share import MatterSmbShare
from app.models.plugin import Matter
from app.models.smb_access_log import SmbAccessLog
from app.models.smb_agent import SmbAgent
from app.models.smb_file_index import SmbFileIndex
from app.models.smb_share import SmbShare
from app.services.cloud_init import MATTER_SUBFOLDERS, matter_relative_path
from app.schemas.smb import (
    AgentRegisterRequest,
    AgentRegisterResponse,
    ContentFetchResult,
    ContentFetchTask,
    MatterSmbShareCreate,
    ShareCreate,
    ShareUpdate,
    SmbSearchResult,
    SyncRequest,
    SyncResponse,
)

settings = get_settings()
logger = logging.getLogger(__name__)

SMB_PAIRING_CODE_TTL_MIN = settings.SMB_PAIRING_CODE_TTL_MIN
SMB_SNIPPET_MAX_CHARS = settings.SMB_SNIPPET_MAX_CHARS
SMB_MAX_FILE_INDEX_PER_SHARE = settings.SMB_MAX_FILE_INDEX_PER_SHARE
REDIS_TASK_TTL = 300  # 5 minutes


def _uuid(val: str | uuid.UUID | None) -> uuid.UUID | None:
    """Coerce a string or UUID value to uuid.UUID, returning None for None/empty."""
    if val is None:
        return None
    if isinstance(val, uuid.UUID):
        return val
    return uuid.UUID(str(val))


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

        code = secrets.token_urlsafe(16)
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
        result = await db.execute(stmt)
        agent = result.scalar_one_or_none()

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

        # Set tenant context so subsequent RLS-aware queries work
        await set_tenant_context(db, str(agent.tenant_id))

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
        await db.flush()

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
                SmbFileIndex.share_id == share_uuid,
                SmbFileIndex.is_deleted.is_(False),
            )
        )
        current_count = count_result.scalar_one()

        snippet_cap = SMB_SNIPPET_MAX_CHARS

        for entry in sync_data.files:
            try:
                if current_count >= SMB_MAX_FILE_INDEX_PER_SHARE:
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
                        SmbFileIndex.path == path,
                    )
                    .values(is_deleted=True)
                )
                deleted += result.rowcount
            except Exception as exc:
                errors.append({"path": path, "error": str(exc)})

        await db.flush()
        return SyncResponse(synced=synced, deleted=deleted, errors=errors)

    async def get_pending_tasks(
        self,
        db: AsyncSession,
        agent_id: str,
        redis=None,
        limit: int = 10,
    ) -> list[ContentFetchTask]:
        """Return pending content fetch tasks for this agent.

        Tasks are stored in Redis under key ``smb_task_pending:<agent_id>:<task_id>``.
        """
        if not redis:
            return []

        tasks: list[ContentFetchTask] = []
        keys = await redis.keys(f"smb_task_pending:{agent_id}:*")
        for key in keys[:limit]:
            raw = await redis.get(key)
            if raw:
                data = json.loads(raw if isinstance(raw, str) else raw.decode())
                tasks.append(ContentFetchTask(**data))
        return tasks

    async def submit_task_result(
        self,
        db: AsyncSession,
        agent_id: str,
        task_id: str,
        result: ContentFetchResult,
        redis=None,
    ) -> None:
        """Store content fetch result in Redis with 5-min TTL."""
        if not redis:
            logger.warning("Redis not available, cannot store task result")
            return

        payload = json.dumps(
            {
                "content": result.content,
                "truncated": result.truncated,
                "error": result.error,
            }
        )
        await redis.set(f"smb_task:{task_id}", payload, ex=REDIS_TASK_TTL)
        await redis.delete(f"smb_task_pending:{agent_id}:{task_id}")

    async def request_content_fetch(
        self,
        db: AsyncSession,
        tenant_id: str,
        user_id: str,
        file_id: str,
        conversation_id: str | None,
        reason: str,
        redis=None,
    ) -> tuple[str, str]:
        """Create a content fetch task, log access, return (task_id, agent_id).

        Pushes task into Redis for the agent to pick up.
        """
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
        if not agent_id:
            raise ValueError("No agent assigned to file")

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
            reason=reason,
        )

        if redis:
            pending_key = f"smb_task_pending:{agent_id}:{task_id}"
            await redis.set(
                pending_key,
                task.model_dump_json(),
                ex=REDIS_TASK_TTL,
            )

        return task_id, agent_id

    async def get_content_result(self, task_id: str, redis=None) -> str | None:
        """Poll Redis for content fetch result. Return content if available, None if pending."""
        if not redis:
            return None

        raw = await redis.get(f"smb_task:{task_id}")
        if raw:
            data = json.loads(raw if isinstance(raw, str) else raw.decode())
            return data.get("content")
        return None

    async def poll_content_result(
        self,
        task_id: str,
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
            content = await self.get_content_result(task_id, redis=redis)
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
            bindings = (
                await db.execute(
                    select(MatterSmbShare).where(
                        MatterSmbShare.matter_id == _uuid(matter_id),
                        MatterSmbShare.tenant_id == tenant_uuid,
                    )
                )
            ).scalars().all()
            if not bindings:
                return []

            share_ids = [binding.share_id for binding in bindings]
            stmt = stmt.where(SmbFileIndex.share_id.in_(share_ids))

            folder_filters = []
            for binding in bindings:
                prefix = (binding.folder_path or "").strip().replace("\\", "/")
                if prefix:
                    normalized_prefix = prefix.rstrip("/")
                    windows_prefix = normalized_prefix.replace("/", "\\")
                    folder_filters.append(
                        SmbFileIndex.path.ilike(f"%{normalized_prefix}%")
                    )
                    if windows_prefix != normalized_prefix:
                        folder_filters.append(
                            SmbFileIndex.path.ilike(f"%{windows_prefix}%")
                        )
            if folder_filters:
                from sqlalchemy import or_

                stmt = stmt.where(or_(*folder_filters))

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

    async def create_share(
        self,
        db: AsyncSession,
        agent_id: str,
        tenant_id: str,
        data: ShareCreate,
    ) -> SmbShare:
        """Create a new SMB share configuration for an agent."""
        await set_tenant_context(db, tenant_id)

        share = SmbShare(
            agent_id=_uuid(agent_id),
            tenant_id=_uuid(tenant_id),
            share_path=data.share_path,
            display_name=data.display_name,
            file_extensions=data.file_extensions,
            max_depth=data.max_depth or 10,
            scan_schedule=data.scan_schedule or "0 */6 * * *",
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

        if data.display_name is not None:
            share.display_name = data.display_name
        if data.file_extensions is not None:
            share.file_extensions = data.file_extensions
        if data.max_depth is not None:
            share.max_depth = data.max_depth
        if data.scan_schedule is not None:
            share.scan_schedule = data.scan_schedule

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
            delete(SmbFileIndex).where(SmbFileIndex.share_id == share_uuid)
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

        folder_path = data.folder_path or matter_relative_path(matter.slug)
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
                sub: f"{folder_path.rstrip('/')}/{sub}"
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

        agent_count = (
            await db.execute(
                select(func.count(SmbAgent.id)).where(SmbAgent.tenant_id == tid)
            )
        ).scalar_one()

        active_agents = (
            await db.execute(
                select(func.count(SmbAgent.id)).where(
                    SmbAgent.tenant_id == tid,
                    SmbAgent.status == "active",
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

        return {
            "agent_count": agent_count,
            "active_agents": active_agents,
            "share_count": share_count,
            "file_count": file_count,
            "total_size_bytes": int(total_size),
            "recent_fetches_24h": recent_fetches,
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
