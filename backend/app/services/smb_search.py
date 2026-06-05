"""SMB file search service — tsvector full-text search on on-prem file metadata.

Searches the smb_file_index table using PostgreSQL full-text search (tsvector/GIN).
Content is never stored — only snippets. When the caller needs full content,
the service dispatches a content fetch task to the relay agent and awaits
the result (with configurable timeout).
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, func, and_, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import set_tenant_context
from app.models.smb_file_index import SmbFileIndex
from app.models.smb_agent import SmbAgent
from app.models.smb_access_log import SmbAccessLog
from app.models.smb_share import SmbShare
from app.models.matter_smb_share import MatterSmbShare

settings = get_settings()
logger = logging.getLogger(__name__)


class SmbSearchResult:
    """A single SMB file search hit with snippet (no content)."""

    def __init__(
        self,
        id: str,
        path: str,
        filename: str,
        ext: str | None,
        snippet: str | None,
        owner: str | None,
        size_bytes: int | None,
        modified_time: datetime | None,
        created_time: datetime | None,
        share_id: str | None,
        score: float | None = None,
    ):
        self.id = id
        self.path = path
        self.filename = filename
        self.ext = ext
        self.snippet = snippet
        self.owner = owner
        self.size_bytes = size_bytes
        self.modified_time = modified_time
        self.created_time = created_time
        self.share_id = share_id
        self.score = score

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "path": self.path,
            "filename": self.filename,
            "ext": self.ext,
            "snippet": self.snippet,
            "owner": self.owner,
            "size_bytes": self.size_bytes,
            "modified_time": self.modified_time.isoformat() if self.modified_time else None,
            "created_time": self.created_time.isoformat() if self.created_time else None,
            "share_id": str(self.share_id) if self.share_id else None,
            "score": self.score,
        }


async def search_smb_files(
    db: AsyncSession,
    tenant_id: str,
    query: str,
    matter_id: str | None = None,
    file_extensions: list[str] | None = None,
    limit: int = 20,
) -> list[SmbSearchResult]:
    """Full-text search on smb_file_index using tsvector.

    If matter_id is provided, scope search to shares bound to that matter
    via matter_smb_shares. Otherwise search all files for the tenant.

    Args:
        db: Database session.
        tenant_id: Tenant ID for RLS.
        query: Natural-language search query.
        matter_id: Optional matter ID to scope search.
        file_extensions: Optional list of extensions to filter (e.g. ['.pdf', '.docx']).
        limit: Maximum results to return.

    Returns:
        List of SmbSearchResult objects (snippets only, no content).
    """
    if not settings.SMB_ENABLED:
        return []

    await set_tenant_context(db, tenant_id)

    ts_query = func.plainto_tsquery("english", query)
    rank_expr = func.ts_rank(SmbFileIndex.search_vector, ts_query).label("rank")

    filters = [
        SmbFileIndex.tenant_id == uuid.UUID(tenant_id),
        SmbFileIndex.is_deleted == False,  # noqa: E712
        SmbFileIndex.search_vector.op("@@")(ts_query),
    ]

    if file_extensions:
        filters.append(SmbFileIndex.ext.in_(file_extensions))

    if matter_id:
        share_ids_subq = (
            select(MatterSmbShare.share_id)
            .where(
                MatterSmbShare.tenant_id == uuid.UUID(tenant_id),
                MatterSmbShare.matter_id == uuid.UUID(matter_id),
            )
            .subquery()
        )
        filters.append(SmbFileIndex.share_id.in_(select(share_ids_subq.c.share_id)))

    stmt = (
        select(SmbFileIndex, rank_expr)
        .where(and_(*filters))
        .order_by(rank_expr.desc())
        .limit(limit)
    )

    result = await db.execute(stmt)
    rows = result.all()

    results = []
    for row in rows:
        f = row[0]
        results.append(
            SmbSearchResult(
                id=str(f.id),
                path=f.path,
                filename=f.filename,
                ext=f.ext,
                snippet=f.snippet[: settings.SMB_SNIPPET_MAX_CHARS] if f.snippet else None,
                owner=f.owner,
                size_bytes=f.size_bytes,
                modified_time=f.modified_time,
                created_time=f.created_time,
                share_id=str(f.share_id) if f.share_id else None,
                score=float(row[1]) if row[1] else None,
            )
        )

    return results


async def request_content_fetch(
    db: AsyncSession,
    tenant_id: str,
    user_id: str,
    file_id: str,
    conversation_id: str | None = None,
    reason: str = "search_result",
    redis_client=None,
) -> tuple[str, str | None]:
    """Request content fetch from the relay agent.

    Creates a task in Redis that the agent will pick up on its next poll.
    Returns (task_id, agent_id). The agent_id is None if no active agent is found.

    Args:
        db: Database session.
        tenant_id: Tenant ID for RLS.
        user_id: User requesting the content.
        file_id: SmbFileIndex row ID.
        conversation_id: Optional conversation ID for context.
        reason: Why content was requested (search_result/manual/content_fetch).
        redis_client: Redis client from app.state.

    Returns:
        Tuple of (task_id, agent_id). agent_id is None if no agent available.
    """
    await set_tenant_context(db, tenant_id)

    file_result = await db.execute(
        select(SmbFileIndex).where(
            SmbFileIndex.id == uuid.UUID(file_id),
            SmbFileIndex.tenant_id == uuid.UUID(tenant_id),
            SmbFileIndex.is_deleted == False,  # noqa: E712
        )
    )
    file_row = file_result.scalar_one_or_none()
    if not file_row:
        raise ValueError(f"File {file_id} not found or not accessible")

    agent_result = await db.execute(
        select(SmbAgent).where(
            SmbAgent.tenant_id == uuid.UUID(tenant_id),
            SmbAgent.status == "active",
        )
    )
    agent = agent_result.scalar_one_or_none()
    agent_id = str(agent.id) if agent else None

    task_id = str(uuid.uuid4())

    access_log = SmbAccessLog(
        tenant_id=uuid.UUID(tenant_id),
        user_id=uuid.UUID(user_id),
        agent_id=agent.id if agent else None,
        file_path=file_row.path,
        conversation_id=uuid.UUID(conversation_id) if conversation_id else None,
        access_reason=reason,
        bytes_sent=None,
    )
    db.add(access_log)
    await db.commit()

    if redis_client and agent_id:
        import json

        await redis_client.setex(
            f"smb_task_pending:{agent_id}:{task_id}",
            settings.SMB_CONTENT_FETCH_TIMEOUT,
            json.dumps(
                {
                    "task_id": task_id,
                    "file_path": file_row.path,
                    "reason": reason,
                }
            ),
        )

    return task_id, agent_id


async def get_content_result(
    task_id: str,
    redis_client=None,
    timeout_seconds: int | None = None,
) -> str | None:
    """Poll Redis for a content fetch task result.

    Args:
        task_id: The task ID returned by request_content_fetch.
        redis_client: Redis client from app.state.
        timeout_seconds: Max seconds to wait. Defaults to SMB_CONTENT_FETCH_TIMEOUT.

    Returns:
        File content string if available, None if task not yet completed.
    """
    if not redis_client:
        return None

    timeout = timeout_seconds or settings.SMB_CONTENT_FETCH_TIMEOUT
    key = f"smb_task:{task_id}"
    deadline = asyncio.get_event_loop().time() + timeout

    while asyncio.get_event_loop().time() < deadline:
        result = await redis_client.get(key)
        if result is not None:
            await redis_client.delete(key)
            return result if isinstance(result, str) else result.decode()
        await asyncio.sleep(2)

    return None


async def build_smb_context(
    results: list[SmbSearchResult],
    include_snippets: bool = True,
) -> str:
    """Format SMB file search results into an LLM context string.

    Similar to build_cloud_context but for on-prem file metadata.
    Content is never included — only snippets and file metadata.
    """
    if not results:
        return ""

    parts = []
    for i, hit in enumerate(results, start=1):
        header = f"[S{i}] On-prem: {hit.filename}"
        if hit.ext:
            header += f" ({hit.ext})"
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


async def get_smb_stats(db: AsyncSession, tenant_id: str) -> dict:
    """Get SMB stats for admin dashboard.

    Returns agent count, share count, file count, and last activity timestamps.
    """
    await set_tenant_context(db, tenant_id)

    agent_count = await db.execute(
        select(func.count(SmbAgent.id)).where(SmbAgent.tenant_id == uuid.UUID(tenant_id))
    )
    active_agents = await db.execute(
        select(func.count(SmbAgent.id)).where(
            SmbAgent.tenant_id == uuid.UUID(tenant_id),
            SmbAgent.status == "active",
        )
    )
    share_count = await db.execute(
        select(func.count(SmbShare.id)).where(SmbShare.tenant_id == uuid.UUID(tenant_id))
    )
    file_count = await db.execute(
        select(func.count(SmbFileIndex.id)).where(
            SmbFileIndex.tenant_id == uuid.UUID(tenant_id),
            SmbFileIndex.is_deleted == False,  # noqa: E712
        )
    )
    last_heartbeat = await db.execute(
        select(func.max(SmbAgent.last_heartbeat)).where(
            SmbAgent.tenant_id == uuid.UUID(tenant_id),
        )
    )
    last_sync = await db.execute(
        select(func.max(SmbShare.last_scan_at)).where(
            SmbShare.tenant_id == uuid.UUID(tenant_id),
        )
    )

    return {
        "total_agents": agent_count.scalar_one(),
        "active_agents": active_agents.scalar_one(),
        "total_shares": share_count.scalar_one(),
        "total_files": file_count.scalar_one(),
        "last_agent_heartbeat": last_heartbeat.scalar_one().isoformat()
        if last_heartbeat.scalar_one()
        else None,
        "last_file_sync": last_sync.scalar_one().isoformat()
        if last_sync.scalar_one()
        else None,
    }