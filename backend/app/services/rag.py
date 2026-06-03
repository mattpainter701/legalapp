from typing import List, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from app.config import get_settings
from app.services.embeddings import EmbeddingService

settings = get_settings()


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
            1 - (embedding <=> :vec::vector) AS similarity
        FROM chunks
        WHERE tenant_id = CAST(:tenant_id AS uuid)
          AND embedding IS NOT NULL
        ORDER BY embedding <=> :vec::vector
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
            1 - (embedding <=> :vec::vector) AS similarity
        FROM public_chunks
        WHERE embedding IS NOT NULL
        ORDER BY embedding <=> :vec::vector
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
        hit = item.get("hit", {})
        content = item.get("content", "")
        if not content:
            content = hit.get("snippet", "")

        source_label = f"{hit.get('provider', 'cloud')}/{hit.get('source', 'unknown')}"
        title = hit.get("title", "Untitled")
        url = hit.get("url", "")
        modified = hit.get("modified_time", "")

        header = f"[C{i}] {source_label}: {title}"
        if url:
            header += f"\n    URL: {url}"
        if modified:
            header += f"\n    Modified: {modified}"

        parts.append(
            f"{header}\nContent:\n{content[:2000]}\n" + "-" * 60,
        )

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
) -> tuple[str, list[dict], list[dict]]:
    """
    Hybrid RAG pipeline: pgvector search + cloud search.

    Runs both paths in parallel (pgvector is always attempted; cloud search
    only if services are provided and tenant has integrations).

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
    if cloud_search_service and retrieval_planner:
        from app.config import get_settings as _get_settings

        _settings = _get_settings()
        if _settings.CLOUD_SEARCH_ENABLED:
            try:
                plan = await retrieval_planner.plan(
                    user_question=question,
                    tenant_name=tenant_name,
                    matter_context=matter_context_str,
                )
                if plan and plan.get("should_search"):
                    cloud_hits = await cloud_search_service.search(
                        db=db,
                        plan=plan,
                        tenant_id=tenant_id,
                        user_id=user_id,
                    )
                    if cloud_hits:
                        hits_with_content = await cloud_search_service.fetch_contents(
                            db=db,
                            hits=cloud_hits,
                            tenant_id=tenant_id,
                            max_chars=_settings.CLOUD_SEARCH_HIT_CONTENT_CHARS,
                        )
                        cloud_context = await build_cloud_context(
                            hits_with_content,
                        )
            except Exception:
                # Cloud search is additive — failure must not break chat
                pass

    # 3. Merge contexts — cloud results after pgvector
    if cloud_context:
        context_str = (
            f"{pgvector_context}\n\n--- Cloud Search Results ---\n\n{cloud_context}"
        )
    else:
        context_str = pgvector_context

    return context_str, chunks, cloud_hits
