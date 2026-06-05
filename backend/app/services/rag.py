import uuid
from typing import List, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text, func as sa_func

from app.config import get_settings
from app.services.embeddings import EmbeddingService

settings = get_settings()


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
    searched by search_public_chunks().
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


async def build_rag_context(chunks: List[dict]) -> str:
    """Format retrieved chunks into a context string with citation info."""
    if not chunks:
        return "No relevant legal sources found in the database."

    parts = []
    for i, chunk in enumerate(chunks, start=1):
        case_name = chunk.get("case_name") or "Unknown Case"
        citation = chunk.get("citation") or "No Citation"
        court = chunk.get("court") or ""
        decision_date = chunk.get("decision_date") or ""
        content = chunk.get("content", "")
        similarity = chunk.get("similarity", 0.0)

        header_parts = [f"[{i}] {case_name}"]
        if citation:
            header_parts.append(f"Citation: {citation}")
        if court:
            header_parts.append(f"Court: {court}")
        if decision_date:
            header_parts.append(f"Date: {decision_date}")

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
    include_public: bool = True,
) -> Tuple[str, List[dict]]:
    """
    Full RAG pipeline: embed question, search chunks, build context.
    Returns (context_string, chunks_list).
    """
    query_embedding = await embedding_service.embed_text(question)

    if query_embedding is None:
        # Embeddings unavailable — return empty context (chat still works, no RAG)
        chunks = []
    else:
        chunks = await search_chunks(
            db=db,
            query_embedding=query_embedding,
            tenant_id=tenant_id,
            top_k=settings.RAG_TOP_K,
        )

    if include_public:
        public_embedding = await embedding_service.embed_public_query(question)
        if public_embedding is not None:
            public_chunks = await search_public_chunks(
                db=db,
                query_embedding=public_embedding,
                top_k=settings.PUBLIC_RAG_TOP_K,
            )
            chunks.extend(public_chunks)

    context_str = await build_rag_context(chunks)
    return context_str, chunks


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

        header = f"[C{i}] {source_label}: {title}"
        if url:
            header += f"\n    URL: {url}"
        if modified:
            header += f"\n    Modified: {modified}"

        parts.append(f"{header}\nContent:\n{content[:2000]}\n" + "-" * 60)

    return "\n\n".join(parts)


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
) -> tuple[str, list[dict], list[dict]]:
    """
    Hybrid RAG pipeline: pgvector search + cloud search + SMB file search.

    Runs all paths in parallel (pgvector is always attempted; cloud and SMB
    search only if services are provided and feature is enabled).

    Returns (context_string, chunks_list, cloud_hits_list).
    The caller is responsible for merging context strings if both return results.
    """
    # 1. Standard pgvector RAG (always run)
    pgvector_context, chunks = await full_rag_query(
        db=db,
        embedding_service=embedding_service,
        question=question,
        tenant_id=tenant_id,
        include_public=include_public,
    )

    # 2. Cloud search (only if services are wired and cloud search is enabled)
    cloud_hits = []
    cloud_context = ""
    smb_context = ""
    if cloud_search_service and retrieval_planner:
        from app.config import get_settings as _get_settings

        _settings = _get_settings()
        # Determine which providers are connected for cloud search
        connected = await _connected_providers(db, tenant_id, user_id)

        # Check if SMB is enabled for this tenant
        smb_enabled = _settings.SMB_ENABLED
        if smb_enabled:
            from app.models.smb_agent import SmbAgent

            active_agents = await db.execute(
                select(sa_func.count(SmbAgent.id)).where(
                    SmbAgent.tenant_id == uuid.UUID(tenant_id)
                    if isinstance(tenant_id, str)
                    else SmbAgent.tenant_id,
                    SmbAgent.status == "active",
                )
            )
            if active_agents.scalar_one() == 0:
                smb_enabled = False

        plan = None
        if connected or smb_enabled:
            try:
                plan = await retrieval_planner.plan(
                    user_question=question,
                    tenant_name=tenant_name,
                    matter_context=matter_context_str,
                    active_providers=connected if connected else None,
                    smb_enabled=smb_enabled,
                )
            except Exception:
                pass  # Non-fatal

        if plan and plan.get("should_search"):
            try:
                sources = plan.get("sources", [])
                # Cloud search (Google/Microsoft sources)
                if _settings.CLOUD_SEARCH_ENABLED:
                    cloud_sources = [s for s in sources if s not in ("smb",)]
                    if cloud_sources and connected:
                        cloud_plan = {**plan, "sources": cloud_sources}
                        cloud_hits = await cloud_search_service.search(
                            db=db,
                            plan=cloud_plan,
                            tenant_id=tenant_id,
                            user_id=user_id,
                        )
                        if cloud_hits:
                            hits_with_content = (
                                await cloud_search_service.fetch_contents(
                                    db=db,
                                    hits=cloud_hits,
                                    tenant_id=tenant_id,
                                    max_chars=_settings.CLOUD_SEARCH_HIT_CONTENT_CHARS,
                                )
                            )
                            cloud_context = await build_cloud_context(
                                hits_with_content,
                            )

                # SMB search (on-prem file shares)
                if _settings.SMB_ENABLED and "smb" in sources:
                    try:
                        from app.services.smb import smb_service

                        smb_results = await smb_service.search_files(
                            db=db,
                            tenant_id=tenant_id,
                            query=" ".join(plan.get("keywords", [question])),
                            matter_id=matter_id,
                            limit=plan.get("max_hits", 10),
                        )
                        if smb_results:
                            smb_context = await smb_service.build_smb_context(
                                smb_results
                            )
                    except Exception:
                        pass  # SMB search is additive — failure must not break chat

            except Exception:
                # Cloud search is additive — failure must not break chat
                pass

    # 3. Merge contexts — cloud and SMB results after pgvector
    parts = [pgvector_context]
    if cloud_context:
        parts.append(f"--- Cloud Search Results ---\n\n{cloud_context}")
    if smb_context:
        parts.append(f"--- On-Prem File Share Results ---\n\n{smb_context}")
    context_str = "\n\n".join(parts) if len(parts) > 1 else pgvector_context

    return context_str, chunks, cloud_hits
