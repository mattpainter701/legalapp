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

    result = await db.execute(sql, {"tenant_id": tenant_id, "top_k": top_k, "vec": vec_str})
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
