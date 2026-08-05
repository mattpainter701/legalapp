import asyncio
import json
import logging
import time
import uuid
from typing import Any, List, Tuple

import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text, func as sa_func

from app.config import get_settings
from app.database import async_session_maker, set_tenant_context
from app.services.embeddings import EmbeddingService
from app.services.mcp_product import record_internal_chat_mcp_usage

settings = get_settings()
logger = logging.getLogger(__name__)


async def _connected_providers(
    db: AsyncSession,
    tenant_id: str,
    user_id: str | None,
) -> list[str]:
    """Return the list of cloud providers this tenant/user can actually search.

    Combines active tenant-wide credentials with the calling user's own OAuth
    tokens so the planner only targets providers that are connected.
    """
    from app.models.tenant_credential import TenantCredential
    from app.models.user_oauth_token import UserOAuthToken

    providers: set[str] = set()

    cred_rows = await db.execute(
        select(TenantCredential.provider).where(
            TenantCredential.tenant_id == tenant_id,
            TenantCredential.is_active,
        )
    )
    providers.update(p for (p,) in cred_rows.all() if p)

    if user_id:
        user_rows = await db.execute(
            select(UserOAuthToken.provider).where(
                UserOAuthToken.tenant_id == tenant_id,
                UserOAuthToken.user_id == user_id,
            )
        )
        providers.update(p for (p,) in user_rows.all() if p)

    return [p for p in ("google", "microsoft") if p in providers]


class RAGService:
    pass


# ── Reciprocal Rank Fusion ─────────────────────────────────────────────────


def reciprocal_rank_fusion(
    dense_results: list[dict],
    fts_results: list[dict],
    k: int = 60,
    dense_weight: float = 0.6,
    fts_weight: float = 0.4,
) -> list[dict]:
    """Merge dense and FTS result sets via Reciprocal Rank Fusion.

    Each result earns score = weight / (k + rank) for each list it appears in.
    Results are returned sorted by fused score descending.

    The default weighting (0.6 dense / 0.4 FTS) slightly favors semantic
    similarity over exact keyword match, which works well for legal queries
    where conceptual relevance matters more than exact string match.
    """
    scores: dict[str, tuple[float, dict]] = {}

    for rank, item in enumerate(dense_results):
        item_id = item.get("id", f"dense_{rank}")
        item["_dense_rank"] = rank + 1
        item["_fts_rank"] = None
        scores[item_id] = (dense_weight / (k + rank + 1), item)

    for rank, item in enumerate(fts_results):
        item_id = item.get("id", f"fts_{rank}")
        fts_score = fts_weight / (k + rank + 1)
        if item_id in scores:
            existing_score, existing_item = scores[item_id]
            existing_item["_fts_rank"] = rank + 1
            scores[item_id] = (existing_score + fts_score, existing_item)
        else:
            item["_dense_rank"] = None
            item["_fts_rank"] = rank + 1
            scores[item_id] = (fts_score, item)

    fused = sorted(scores.values(), key=lambda x: x[0], reverse=True)
    results = []
    for score, item in fused:
        item["relevance_score"] = round(score, 4)
        results.append(item)
    return results


# ── Full-Text Search (BM25-like via PostgreSQL tsvector) ────────────────────


async def search_chunks_fts(
    db: AsyncSession,
    query: str,
    tenant_id: str,
    top_k: int = 8,
) -> list[dict]:
    """Search chunks by PostgreSQL full-text search (BM25-like).

    Uses plainto_tsquery for user-friendly search that doesn't require
    tsquery syntax. The query is normalized: punctuation stripped, lowercased,
    and OR'd together for broad recall.
    """
    sql = text("""
        SELECT
            id::text,
            content,
            case_name,
            citation,
            court,
            decision_date,
            chunk_index,
            section_path,
            clause_type,
            ts_rank(fts, plainto_tsquery('english', :query)) AS fts_rank,
            0.0 AS similarity
        FROM chunks
        WHERE tenant_id = CAST(:tenant_id AS uuid)
          AND fts @@ plainto_tsquery('english', :query)
        ORDER BY fts_rank DESC
        LIMIT :top_k
    """)

    result = await db.execute(
        sql,
        {
            "tenant_id": tenant_id,
            "top_k": top_k,
            "query": query,
        },
    )
    rows = result.fetchall()

    return [
        {
            "id": row.id,
            "content": row.content,
            "case_name": row.case_name,
            "citation": row.citation,
            "court": row.court,
            "decision_date": str(row.decision_date) if row.decision_date else None,
            "chunk_index": row.chunk_index,
            "section_path": row.section_path or "",
            "clause_type": row.clause_type or "general",
            "similarity": float(row.fts_rank),
            "source": "tenant_document_fts",
        }
        for row in rows
    ]


async def search_chunks(
    db: AsyncSession,
    query_embedding: List[float],
    tenant_id: str,
    top_k: int = 8,
) -> List[dict]:
    """
    Search chunks by cosine similarity using pgvector.
    Returns top_k most similar private chunks for the given tenant.
    Public CourtListener chunks use a separate BGE embedding space and are
    searched by search_public_chunks() when the MCP server is not configured.
    """
    # Format embedding as a Postgres vector literal
    vec_str = "[" + ",".join(str(v) for v in query_embedding) + "]"

    sql = text("""
        SELECT
            id::text,
            content,
            case_name,
            citation,
            court,
            decision_date,
            chunk_index,
            section_path,
            clause_type,
            1 - (embedding <=> CAST(:vec AS vector)) AS similarity
        FROM chunks
        WHERE tenant_id = CAST(:tenant_id AS uuid)
          AND embedding IS NOT NULL
        ORDER BY embedding <=> CAST(:vec AS vector)
        LIMIT :top_k
    """)

    result = await db.execute(
        sql, {"tenant_id": tenant_id, "top_k": top_k, "vec": vec_str}
    )
    rows = result.fetchall()

    return [
        {
            "id": row.id,
            "content": row.content,
            "case_name": row.case_name,
            "citation": row.citation,
            "court": row.court,
            "decision_date": str(row.decision_date) if row.decision_date else None,
            "chunk_index": row.chunk_index,
            "section_path": row.section_path or "",
            "clause_type": row.clause_type or "general",
            "similarity": float(row.similarity),
            "source": "tenant_document",
        }
        for row in rows
    ]


async def search_public_chunks(
    db: AsyncSession,
    query_embedding: List[float],
    top_k: int = 8,
) -> List[dict]:
    """Search public CourtListener chunks using BGE-384 embeddings."""
    vec_str = "[" + ",".join(str(v) for v in query_embedding) + "]"

    sql = text("""
        SELECT
            id::text,
            content,
            case_name,
            citation,
            court,
            decision_date,
            chunk_index,
            1 - (embedding <=> CAST(:vec AS vector)) AS similarity
        FROM public_chunks
        WHERE embedding IS NOT NULL
        ORDER BY embedding <=> CAST(:vec AS vector)
        LIMIT :top_k
    """)

    result = await db.execute(sql, {"top_k": top_k, "vec": vec_str})
    rows = result.fetchall()

    return [
        {
            "id": row.id,
            "content": row.content,
            "case_name": row.case_name,
            "citation": row.citation,
            "court": row.court,
            "decision_date": str(row.decision_date) if row.decision_date else None,
            "chunk_index": row.chunk_index,
            "similarity": float(row.similarity),
            "source": "public_courtlistener",
        }
        for row in rows
    ]


def _mcp_json_items(response_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract JSON tool content from an MCP tools/call response."""
    items: list[dict[str, Any]] = []
    for content in response_data.get("content", []):
        payload = None
        if isinstance(content, dict) and content.get("type") == "json":
            payload = content.get("json")
        elif isinstance(content, dict) and content.get("type") == "text":
            try:
                payload = json.loads(content.get("text") or "null")
            except json.JSONDecodeError:
                payload = None

        if isinstance(payload, list):
            items.extend(item for item in payload if isinstance(item, dict))
        elif isinstance(payload, dict):
            items.append(payload)
    return items


def _mcp_item_to_chunk(item: dict[str, Any], rank_index: int) -> dict:
    """Map a CourtListener MCP search hit to the chat/RAG chunk contract."""
    chunk_id = str(item.get("chunk_id") or item.get("id") or f"mcp_{rank_index}")
    rank_score = item.get("similarity", item.get("rank"))
    try:
        similarity = float(rank_score)
    except (TypeError, ValueError):
        similarity = 0.0
    if similarity <= 0.0 or similarity > 1.0:
        similarity = max(0.1, 1.0 - (rank_index * 0.05))

    return {
        "id": f"courtlistener:{chunk_id}",
        "content": item.get("content") or "",
        "case_name": item.get("case_name") or "Unknown Case",
        "citation": item.get("citation") or "",
        "court": item.get("court_name") or item.get("court_id") or "",
        "decision_date": str(item.get("date_filed"))
        if item.get("date_filed")
        else None,
        "chunk_index": item.get("chunk_index") or 0,
        "section_path": "CourtListener",
        "clause_type": "public_authority",
        "similarity": similarity,
        "relevance_score": similarity,
        "source": "courtlistener_mcp",
        "retrieval_mode": item.get("search_source") or "unknown",
        "opinion_id": item.get("opinion_id"),
        "cluster_id": item.get("cluster_id"),
        "url": item.get("url") or item.get("source_url"),
        "source_url": item.get("source_url") or item.get("url"),
    }


def _public_source_url(chunk: dict[str, Any]) -> str:
    """Return a provider-safe absolute URL for prompt-visible public sources."""
    value = chunk.get("url") or chunk.get("source_url") or chunk.get("canonical_url")
    if not value:
        opinion_id = chunk.get("opinion_id")
        return (
            f"https://www.courtlistener.com/opinion/{opinion_id}/" if opinion_id else ""
        )
    url = str(value).strip()
    if url.startswith("//"):
        return f"https:{url}"
    if url.startswith("/"):
        return f"https://www.courtlistener.com{url}"
    if url.startswith(("https://", "http://")):
        return url
    return ""


def _mcp_authority_item_to_chunk(item: dict[str, Any], rank_index: int) -> dict:
    """Map a statute/rule/manual MCP hit to the shared chat/RAG contract."""
    chunk_id = str(item.get("chunk_id") or item.get("id") or f"authority_{rank_index}")
    rank_score = item.get("similarity", item.get("rank"))
    try:
        similarity = float(rank_score)
    except (TypeError, ValueError):
        similarity = 0.0
    if similarity <= 0.0 or similarity > 1.0:
        similarity = max(0.1, 1.0 - (rank_index * 0.05))
    source_url = item.get("source_url") or item.get("canonical_url")
    authority_tier = item.get("authority_tier") or "public_authority"
    effective_date = item.get("effective_date") or item.get("publication_date")
    return {
        "id": f"authority:{chunk_id}",
        "content": item.get("content") or "",
        "case_name": item.get("title") or "Legal Authority",
        "citation": item.get("citation") or "",
        "court": item.get("source_name") or item.get("jurisdiction") or "",
        "decision_date": str(effective_date) if effective_date else None,
        "chunk_index": item.get("chunk_index") or 0,
        "section_path": item.get("document_type") or authority_tier,
        "clause_type": authority_tier,
        "similarity": similarity,
        "relevance_score": similarity,
        "source": "legal_authority_mcp",
        "retrieval_mode": item.get("search_source") or "unknown",
        "document_id": item.get("document_id"),
        "source_key": item.get("source_key"),
        "official_status": item.get("official_status"),
        "effective_date": item.get("effective_date"),
        "retrieved_at": item.get("retrieved_at"),
        "last_successful_sync_at": item.get("last_successful_sync_at"),
        "url": source_url,
        "source_url": source_url,
    }


async def _call_public_mcp_search(
    client: httpx.AsyncClient,
    url: str,
    tool_name: str,
    query: str,
    top_k: int,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    started_at = time.monotonic()
    outcome = {
        "tool_name": tool_name,
        "status_code": 503,
        "result_count": 0,
        "latency_ms": 0,
    }
    try:
        response = await client.post(
            url,
            json={"name": tool_name, "arguments": {"query": query, "top_k": top_k}},
            headers={"X-Clarity-Internal-Key": settings.MCP_UPSTREAM_API_KEY},
        )
        outcome["status_code"] = response.status_code
        response.raise_for_status()
        response_data = response.json()
    except Exception:
        logger.exception("Public legal MCP search failed for tool=%s", tool_name)
        outcome["latency_ms"] = int((time.monotonic() - started_at) * 1000)
        return None, outcome
    if response_data.get("isError"):
        logger.warning("Public legal MCP returned an error for tool=%s", tool_name)
        outcome["status_code"] = 502
        outcome["latency_ms"] = int((time.monotonic() - started_at) * 1000)
        return None, outcome
    outcome["latency_ms"] = int((time.monotonic() - started_at) * 1000)
    return response_data, outcome


class MCPPublicResults(list):
    """Public chunks plus per-tool outcomes used by internal telemetry."""

    def __init__(self, values: list[dict], outcomes: list[dict[str, Any]]):
        super().__init__(values)
        self.mcp_outcomes = outcomes


async def search_courtlistener_mcp(
    query: str,
    top_k: int = 8,
) -> list[dict]:
    """Search public authority through the configured CourtListener MCP server."""
    if not settings.MCP_SERVER_URL:
        return []

    url = f"{settings.MCP_SERVER_URL.rstrip('/')}/api/mcp/tools/call"
    if not settings.MCP_UPSTREAM_API_KEY:
        logger.error("CourtListener MCP upstream authentication is not configured")
        return MCPPublicResults(
            [],
            [
                {
                    "tool_name": tool_name,
                    "status_code": 503,
                    "result_count": 0,
                    "latency_ms": 0,
                }
                for tool_name in ("search_caselaw", "search_legal_authorities")
            ],
        )
    timeout = httpx.Timeout(12.0, connect=3.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        case_result, authority_result = await asyncio.gather(
            _call_public_mcp_search(client, url, "search_caselaw", query, top_k),
            _call_public_mcp_search(
                client, url, "search_legal_authorities", query, top_k
            ),
        )
    case_response, case_outcome = case_result
    authority_response, authority_outcome = authority_result

    case_chunks = [
        _mcp_item_to_chunk(item, index)
        for index, item in enumerate(_mcp_json_items(case_response or {}))
        if item.get("content")
    ]
    authority_chunks = [
        _mcp_authority_item_to_chunk(item, index)
        for index, item in enumerate(_mcp_json_items(authority_response or {}))
        if item.get("content")
    ]
    combined = case_chunks + authority_chunks
    combined.sort(
        key=lambda item: float(item.get("relevance_score") or 0.0), reverse=True
    )
    case_outcome["result_count"] = len(case_chunks)
    authority_outcome["result_count"] = len(authority_chunks)
    return MCPPublicResults(
        combined[:top_k],
        [case_outcome, authority_outcome],
    )


async def build_rag_context(chunks: List[dict]) -> str:
    """Format retrieved chunks into a context string with citation and clause metadata."""
    if not chunks:
        return ""

    parts = []
    for i, chunk in enumerate(chunks, start=1):
        case_name = chunk.get("case_name") or "Unknown Case"
        citation = chunk.get("citation") or "No Citation"
        court = chunk.get("court") or ""
        decision_date = chunk.get("decision_date") or ""
        content = chunk.get("content", "")
        similarity = chunk.get("similarity", 0.0)
        section_path = chunk.get("section_path") or ""
        clause_type = chunk.get("clause_type") or "general"
        source = chunk.get("source", "")
        source_url = _public_source_url(chunk)

        source_id = str(chunk.get("id") or "").strip()
        header_parts = [f"[{i}] {case_name}"]
        if source_id:
            header_parts.append(f"[source: {source_id}]")
        if citation:
            header_parts.append(f"Citation: {citation}")
        if source_url:
            header_parts.append(f"URL: {source_url}")
        if court:
            header_parts.append(f"Court: {court}")
        if decision_date:
            header_parts.append(f"Date: {decision_date}")
        if section_path:
            header_parts.append(f"Section: {section_path}")
        if clause_type != "general":
            header_parts.append(f"Type: {clause_type}")
        if "fts" in source:
            header_parts.append("(keyword match)")

        parts.append(
            "\n".join(header_parts)
            + f"\nRelevance: {similarity:.2%}\n"
            + f"Excerpt:\n{content}\n"
            + "-" * 60
        )

    return "\n\n".join(parts)


async def full_rag_query(
    db: AsyncSession,
    embedding_service: EmbeddingService,
    question: str,
    tenant_id: str,
    user_id: str | None = None,
    include_public: bool = True,
) -> Tuple[str, List[dict]]:
    """
    Hybrid RAG pipeline: dense (pgvector cosine) + FTS (PostgreSQL tsvector)
    fused via Reciprocal Rank Fusion, plus optional public CourtListener chunks.

    Embedding calls and FTS search run concurrently. Public authority is retrieved
    through CourtListener MCP when MCP_SERVER_URL is set; otherwise the legacy
    public_chunks/BGE path is used.
    Results are fused per-source: private dense + private FTS via RRF, then
    public chunks are appended (they live in a different embedding space).
    """
    use_mcp_public = include_public and bool(settings.MCP_SERVER_URL)
    public_task = None
    if use_mcp_public:
        # Public authority retrieval does not depend on the tenant embedding.
        # Start it immediately instead of making it wait for embedding + FTS.
        public_task = asyncio.create_task(
            search_courtlistener_mcp(
                query=question,
                top_k=settings.PUBLIC_RAG_TOP_K,
            )
        )
    try:
        if include_public and not use_mcp_public:
            query_embedding, public_embedding, fts_results = await asyncio.gather(
                embedding_service.embed_text(question),
                embedding_service.embed_public_query(question),
                search_chunks_fts(
                    db=db,
                    query=question,
                    tenant_id=tenant_id,
                    top_k=settings.RAG_TOP_K,
                ),
            )
        else:
            query_embedding, fts_results = await asyncio.gather(
                embedding_service.embed_text(question),
                search_chunks_fts(
                    db=db,
                    query=question,
                    tenant_id=tenant_id,
                    top_k=settings.RAG_TOP_K,
                ),
            )
            public_embedding = None
    except BaseException:
        if public_task is not None:
            public_task.cancel()
            await asyncio.gather(public_task, return_exceptions=True)
        raise

    # Dense + public searches in parallel
    dense_task = (
        search_chunks(
            db=db,
            query_embedding=query_embedding,
            tenant_id=tenant_id,
            top_k=settings.RAG_TOP_K,
        )
        if query_embedding is not None
        else _empty_chunks()
    )
    public_search = (
        public_task
        if public_task is not None
        else (
            search_public_chunks(
                db=db,
                query_embedding=public_embedding,
                top_k=settings.PUBLIC_RAG_TOP_K,
            )
            if public_embedding is not None
            else _empty_chunks()
        )
    )
    dense_chunks, public_chunks = await asyncio.gather(
        dense_task,
        public_search,
    )
    mcp_outcomes = list(getattr(public_chunks, "mcp_outcomes", []))
    if (
        use_mcp_public
        and not public_chunks
        and mcp_outcomes
        and all(int(item.get("status_code") or 500) >= 400 for item in mcp_outcomes)
    ):
        # Preserve public research during an MCP outage using the existing
        # local public-authority index. This path is intentionally only used
        # when both MCP tools failed, not for a legitimate zero-result search.
        try:
            public_embedding = await embedding_service.embed_public_query(question)
            public_chunks = await search_public_chunks(
                db=db,
                query_embedding=public_embedding,
                top_k=settings.PUBLIC_RAG_TOP_K,
            )
        except Exception:
            logger.exception("Legacy public-authority fallback failed")
            public_chunks = []
    if use_mcp_public:
        try:
            async with async_session_maker() as usage_db:
                await set_tenant_context(usage_db, str(tenant_id))
                outcomes = mcp_outcomes or [
                    {
                        "tool_name": "search_caselaw",
                        "status_code": 200,
                        "result_count": len(public_chunks),
                        "latency_ms": None,
                    }
                ]
                for outcome in outcomes:
                    await record_internal_chat_mcp_usage(
                        db=usage_db,
                        tenant_id=uuid.UUID(str(tenant_id)),
                        user_id=uuid.UUID(str(user_id)) if user_id else None,
                        tool_name=str(outcome["tool_name"]),
                        status_code=int(outcome["status_code"]),
                        result_count=int(outcome.get("result_count") or 0),
                        latency_ms=outcome.get("latency_ms"),
                    )
        except Exception:
            logger.exception("Failed to record internal CourtListener MCP usage")

    # Fuse private dense + FTS results via RRF
    fused_private = reciprocal_rank_fusion(
        dense_results=dense_chunks,
        fts_results=fts_results,
    )

    # Limit fused results and append public chunks
    chunks = fused_private[: settings.RAG_TOP_K] + public_chunks

    context_str = await build_rag_context(chunks)
    return context_str, chunks


async def _empty_chunks() -> List[dict]:
    return []


async def build_cloud_context(cloud_hits_with_content: list[dict]) -> str:
    """Format cloud search hits into a context string for the LLM."""
    if not cloud_hits_with_content:
        return ""

    parts = []
    for i, item in enumerate(cloud_hits_with_content, start=1):
        hit = item.get("hit")
        if hit is None:
            continue

        hit_dict = hit.to_dict() if hasattr(hit, "to_dict") else dict(hit)
        content = item.get("content") or hit_dict.get("snippet", "")
        if not content:
            continue

        source_label = (
            f"{hit_dict.get('provider', 'cloud')}/{hit_dict.get('source', 'unknown')}"
        )
        title = hit_dict.get("title") or "Untitled"
        url = hit_dict.get("url") or ""
        modified = hit_dict.get("modified_time") or ""

        source_id = cloud_context_source_id(hit_dict)
        header = f"[C{i}] {source_label}: {title}\n[source: {source_id}]"
        if url:
            header += f"\n    URL: {url}"
        if modified:
            header += f"\n    Modified: {modified}"

        parts.append(f"{header}\nContent:\n{content[:2000]}\n" + "-" * 60)

    return "\n\n".join(parts)


def cloud_context_source_id(hit_dict: dict) -> str:
    """Stable id shared by provider context, validation, and API citations."""
    return (
        f"cloud:{hit_dict.get('provider')}:{hit_dict.get('source')}:"
        f"{hit_dict.get('object_id')}"
    )


async def _connected_source_query(
    *,
    question: str,
    tenant_id: str,
    user_id: str | None,
    cloud_search_service,
    retrieval_planner,
    tenant_name: str,
    matter_context_str: str | None,
    matter_id: str | None,
    matter_cloud_folder: dict | None,
) -> tuple[str, list[dict], str]:
    """Search connected cloud/SMB sources using an independent DB session.

    This path is additive. It must not delay or break local/public authority
    retrieval when no provider is connected, the planner times out, or an
    upstream provider is unavailable.
    """
    if not cloud_search_service or not retrieval_planner:
        return "", [], ""

    cloud_hits: list[dict] = []
    cloud_context = ""
    smb_context = ""
    async with async_session_maker() as cloud_db:
        await set_tenant_context(cloud_db, str(tenant_id))
        connected = await _connected_providers(cloud_db, tenant_id, user_id)
        smb_enabled = settings.SMB_ENABLED
        if smb_enabled:
            from app.models.smb_agent import SmbAgent

            active_agents = await cloud_db.execute(
                select(sa_func.count(SmbAgent.id)).where(
                    SmbAgent.tenant_id == uuid.UUID(str(tenant_id)),
                    SmbAgent.status == "active",
                )
            )
            smb_enabled = active_agents.scalar_one() > 0

        if not connected and not smb_enabled:
            return "", [], ""

        try:
            plan = await asyncio.wait_for(
                retrieval_planner.plan(
                    user_question=question,
                    db=cloud_db,
                    tenant_id=tenant_id,
                    tenant_name=tenant_name,
                    matter_context=matter_context_str,
                    active_providers=connected if connected else None,
                    smb_enabled=smb_enabled,
                ),
                timeout=settings.CLOUD_RETRIEVAL_PLANNER_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            logger.info("Connected-source retrieval planner timed out")
            return "", [], ""
        except Exception:
            logger.exception("Connected-source retrieval planner failed")
            return "", [], ""

        if not plan or not plan.get("should_search"):
            return "", [], ""
        sources = plan.get("sources", [])

        if settings.CLOUD_SEARCH_ENABLED:
            cloud_sources = [source for source in sources if source != "smb"]
            if cloud_sources and connected:
                try:
                    cloud_hits = await cloud_search_service.search(
                        db=cloud_db,
                        plan={**plan, "sources": cloud_sources},
                        tenant_id=tenant_id,
                        user_id=user_id,
                        matter_cloud_folder=matter_cloud_folder,
                    )
                    if cloud_hits:
                        hits_with_content = await cloud_search_service.fetch_contents(
                            db=cloud_db,
                            hits=cloud_hits,
                            tenant_id=tenant_id,
                            max_chars=settings.CLOUD_SEARCH_HIT_CONTENT_CHARS,
                            user_id=user_id,
                        )
                        cloud_context = await build_cloud_context(hits_with_content)
                        cloud_hits = [
                            {
                                **(
                                    item["hit"].to_dict()
                                    if hasattr(item.get("hit"), "to_dict")
                                    else dict(item.get("hit") or {})
                                ),
                                "_validation_content": item.get("content") or "",
                            }
                            for item in hits_with_content
                        ]
                except Exception:
                    logger.exception("Connected cloud search failed")

        if smb_enabled and "smb" in sources:
            try:
                from app.services.smb import smb_service

                smb_results = await smb_service.search_files(
                    db=cloud_db,
                    tenant_id=tenant_id,
                    query=" ".join(plan.get("keywords", [question])),
                    matter_id=matter_id,
                    limit=min(int(plan.get("max_hits", 10)), 10),
                )
                if smb_results:
                    smb_context = await smb_service.build_smb_context(smb_results)
            except Exception:
                logger.exception("SMB search failed")

    return cloud_context, cloud_hits, smb_context


async def hybrid_rag_query(
    db: AsyncSession,
    embedding_service: EmbeddingService,
    question: str,
    tenant_id: str,
    user_id: str | None = None,
    include_public: bool = True,
    cloud_search_service=None,  # CloudSearchService | None
    retrieval_planner=None,  # RetrievalPlanner | None
    tenant_name: str = "Legal",
    matter_context_str: str | None = None,
    matter_id: str | None = None,
    matter_cloud_folder: dict | None = None,
) -> tuple[str, list[dict], list[dict]]:
    """
    Hybrid RAG pipeline: pgvector search + cloud search + SMB file search.

    Runs all paths in parallel (pgvector is always attempted; cloud and SMB
    search only if services are provided and feature is enabled).

    Returns (context_string, chunks_list, cloud_hits_list).
    The caller is responsible for merging context strings if both return results.
    """
    local_result, connected_result = await asyncio.gather(
        full_rag_query(
            db=db,
            embedding_service=embedding_service,
            question=question,
            tenant_id=tenant_id,
            user_id=user_id,
            include_public=include_public,
        ),
        _connected_source_query(
            question=question,
            tenant_id=tenant_id,
            user_id=user_id,
            cloud_search_service=cloud_search_service,
            retrieval_planner=retrieval_planner,
            tenant_name=tenant_name,
            matter_context_str=matter_context_str,
            matter_id=matter_id,
            matter_cloud_folder=matter_cloud_folder,
        ),
        return_exceptions=True,
    )

    if isinstance(local_result, BaseException):
        logger.error("Private/public RAG retrieval failed: %s", local_result)
        pgvector_context, chunks = "", []
    else:
        pgvector_context, chunks = local_result

    if isinstance(connected_result, BaseException):
        logger.error("Connected-source retrieval failed: %s", connected_result)
        cloud_context, cloud_hits, smb_context = "", [], ""
    else:
        cloud_context, cloud_hits, smb_context = connected_result

    # 2. Merge contexts — cloud and SMB results after pgvector
    parts = [pgvector_context] if pgvector_context else []
    if cloud_context:
        parts.append(f"--- Cloud Search Results ---\n\n{cloud_context}")
    if smb_context:
        parts.append(f"--- On-Prem File Share Results ---\n\n{smb_context}")
    context_str = "\n\n".join(parts)

    return context_str, chunks, cloud_hits
