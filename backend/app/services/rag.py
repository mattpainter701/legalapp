from typing import List, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from app.config import get_settings
from app.services.embeddings import EmbeddingService

settings = get_settings()

# Sentinel UUID for public case law
PUBLIC_TENANT_UUID = "00000000-0000-0000-0000-000000000001"


class RAGService:
    pass


async def search_chunks(
    db: AsyncSession,
    query_embedding: List[float],
    tenant_id: str,
    include_public: bool = True,
    top_k: int = 8,
) -> List[dict]:
    """
    Search chunks by cosine similarity using pgvector.
    Returns top_k most similar chunks for the given tenant,
    optionally including public case law chunks.
    """
    # Format embedding as a Postgres vector literal
    vec_str = "[" + ",".join(str(v) for v in query_embedding) + "]"

    if include_public:
        tenant_filter = f"""
            (tenant_id = '{tenant_id}'::uuid OR tenant_id = '{PUBLIC_TENANT_UUID}'::uuid)
        """
    else:
        tenant_filter = f"tenant_id = '{tenant_id}'::uuid"

    sql = text(f"""
        SELECT
            id::text,
            content,
            case_name,
            citation,
            court,
            decision_date,
            chunk_index,
            1 - (embedding <=> '{vec_str}'::vector) AS similarity
        FROM chunks
        WHERE {tenant_filter}
          AND embedding IS NOT NULL
        ORDER BY embedding <=> '{vec_str}'::vector
        LIMIT :top_k
    """)

    result = await db.execute(sql, {"top_k": top_k})
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

    chunks = await search_chunks(
        db=db,
        query_embedding=query_embedding,
        tenant_id=tenant_id,
        include_public=include_public,
        top_k=settings.RAG_TOP_K,
    )

    context_str = await build_rag_context(chunks)
    return context_str, chunks
