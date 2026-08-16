import asyncio
import json
import logging
import re
import time
import uuid
from contextlib import asynccontextmanager
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

_RETRIEVAL_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'-]{2,}")
_RETRIEVAL_STOP_WORDS = {
    "about",
    "after",
    "also",
    "and",
    "are",
    "case",
    "for",
    "from",
    "handle",
    "have",
    "how",
    "into",
    "now",
    "out",
    "state",
    "that",
    "the",
    "their",
    "there",
    "this",
    "was",
    "what",
    "when",
    "where",
    "which",
    "who",
    "with",
}
_GENERIC_PRIVATE_RETRIEVAL_TERMS = {
    "california",
    "court",
    "courts",
    "dakota",
    "federal",
    "firm",
    "jurisdiction",
    "law",
    "legal",
    "minnesota",
    "montana",
    "north",
    "rule",
    "rules",
    "south",
    "state",
    "statute",
    "statutory",
}
_PUBLIC_JURISDICTIONS = (
    (re.compile(r"\b(?:north\s+dakota|n\.?d\.?)\b", re.IGNORECASE), "nd", "ND"),
    (re.compile(r"\b(?:south\s+dakota|s\.?d\.?)\b", re.IGNORECASE), "sd", "SD"),
    (re.compile(r"\b(?:minnesota|mn)\b", re.IGNORECASE), "minn", "MN"),
    (re.compile(r"\b(?:montana|mt)\b", re.IGNORECASE), "mont", "MT"),
    (re.compile(r"\b(?:california|calif\.?|ca)\b", re.IGNORECASE), "cal", "CA"),
)
_US_STATE_NAMES = (
    "alabama",
    "alaska",
    "arizona",
    "arkansas",
    "california",
    "colorado",
    "connecticut",
    "delaware",
    "florida",
    "georgia",
    "hawaii",
    "idaho",
    "illinois",
    "indiana",
    "iowa",
    "kansas",
    "kentucky",
    "louisiana",
    "maine",
    "maryland",
    "massachusetts",
    "michigan",
    "minnesota",
    "mississippi",
    "missouri",
    "montana",
    "nebraska",
    "nevada",
    "new hampshire",
    "new jersey",
    "new mexico",
    "new york",
    "north carolina",
    "north dakota",
    "ohio",
    "oklahoma",
    "oregon",
    "pennsylvania",
    "rhode island",
    "south carolina",
    "south dakota",
    "tennessee",
    "texas",
    "utah",
    "vermont",
    "virginia",
    "washington",
    "west virginia",
    "wisconsin",
    "wyoming",
    "district of columbia",
)
_US_STATE_CODES = frozenset(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS "
    "MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV "
    "WI WY DC".split()
)
_US_STATE_NAME_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(name) for name in _US_STATE_NAMES) + r")\b",
    re.IGNORECASE,
)
_FEDERAL_JURISDICTION_RE = re.compile(
    r"\b(?:federal|united\s+states)\b",
    re.IGNORECASE,
)
_FEDERAL_JURISDICTION_ABBREVIATION_RE = re.compile(r"(?:\bUS\b|\bU\.S\.)")


def _retrieval_terms(value: str | None) -> set[str]:
    """Return meaningful lexical terms used by the private-result safety gate."""
    return {
        token.casefold()
        for token in _RETRIEVAL_TOKEN_RE.findall(value or "")
        if token.casefold() not in _RETRIEVAL_STOP_WORDS
    }


def filter_private_retrieval_results(question: str, chunks: list[dict]) -> list[dict]:
    """Remove nearest-neighbour filler that has no defensible query relationship.

    A vector index always returns *something*, even when every document is about a
    different matter.  Keep a result when it has either strong semantic similarity
    or meaningful lexical overlap.  This is deliberately applied only to private
    firm material; public-authority search has its own ranking and provenance.
    """
    query_terms = _retrieval_terms(question)
    topic_terms = query_terms.difference(_GENERIC_PRIVATE_RETRIEVAL_TERMS)
    required_overlap = 1 if len(query_terms) <= 2 else 2
    retained: list[dict] = []
    for chunk in chunks:
        searchable = " ".join(
            str(chunk.get(key) or "")
            for key in (
                "document_title",
                "case_name",
                "citation",
                "section_path",
                "content",
            )
        )
        searchable_terms = _retrieval_terms(searchable)
        overlap = len(query_terms.intersection(searchable_terms))
        topic_overlap = len(topic_terms.intersection(searchable_terms))
        try:
            similarity = float(chunk.get("similarity") or 0.0)
        except (TypeError, ValueError):
            similarity = 0.0
        # Generic legal/location words (for example "California jurisdiction")
        # occur throughout contracts and retainers. They cannot, by themselves,
        # make those documents relevant to a divorce/custody question.
        if similarity >= 0.70 or (topic_overlap > 0 and overlap >= required_overlap):
            chunk["lexical_overlap"] = overlap
            chunk["topic_overlap"] = topic_overlap
            retained.append(chunk)
    return retained


def filter_public_retrieval_results(question: str, chunks: list[dict]) -> list[dict]:
    """Drop public-authority hits that only match generic legal/location terms.

    The MCP FTS query intentionally uses broad OR recall.  Without a second-stage
    gate, a top-ranked passage can share only a word such as ``court`` or the
    jurisdiction name and still be exposed as an apparent source.  Keep strong
    semantic matches, or require meaningful issue-term overlap for lexical hits.
    """

    query_terms = _retrieval_terms(question)
    topic_terms = query_terms.difference(_GENERIC_PRIVATE_RETRIEVAL_TERMS)
    required_overlap = 1 if len(topic_terms) <= 2 else 2
    retained: list[dict] = []
    query_sequence = [
        token.casefold()
        for token in _RETRIEVAL_TOKEN_RE.findall(question or "")
        if token.casefold() not in _RETRIEVAL_STOP_WORDS
    ]
    query_bigrams = set(zip(query_sequence, query_sequence[1:]))
    for chunk in chunks:
        searchable = " ".join(
            str(chunk.get(key) or "")
            for key in (
                "case_name",
                "title",
                "citation",
                "section_path",
                "clause_type",
                "content",
            )
        )
        searchable_terms = _retrieval_terms(searchable)
        searchable_sequence = [
            token.casefold()
            for token in _RETRIEVAL_TOKEN_RE.findall(searchable)
            if token.casefold() not in _RETRIEVAL_STOP_WORDS
        ]
        searchable_bigrams = set(zip(searchable_sequence, searchable_sequence[1:]))
        overlap = len(query_terms.intersection(searchable_terms))
        topic_overlap = len(topic_terms.intersection(searchable_terms))
        try:
            similarity = float(chunk.get("similarity") or 0.0)
        except (TypeError, ValueError):
            similarity = 0.0
        strong_semantic_match = similarity >= 0.78
        corroborated_semantic_match = similarity >= 0.65 and topic_overlap > 0
        meaningful_lexical_match = (
            topic_overlap >= required_overlap and overlap >= required_overlap
        )
        lexical_coverage = topic_overlap / max(1, len(topic_terms))
        phrase_coverage = len(query_bigrams.intersection(searchable_bigrams)) / max(
            1, len(query_bigrams)
        )
        evidence_score = min(
            1.0,
            (0.65 * similarity) + (0.25 * lexical_coverage) + (0.10 * phrase_coverage),
        )
        if (
            strong_semantic_match
            or corroborated_semantic_match
            or meaningful_lexical_match
        ):
            chunk["lexical_overlap"] = overlap
            chunk["topic_overlap"] = topic_overlap
            chunk["retrieval_score"] = chunk.get("relevance_score")
            chunk["evidence_relevance_score"] = round(evidence_score, 4)
            chunk["relevance_score"] = round(evidence_score, 4)
            retained.append(chunk)
    retained.sort(
        key=lambda item: float(item.get("evidence_relevance_score") or 0.0),
        reverse=True,
    )
    return retained


def _explicit_public_jurisdictions(query: str) -> list[tuple[str, str]]:
    """Return CourtListener/authority jurisdiction ids in mention order."""
    matches: list[tuple[int, str, str]] = []
    for pattern, courtlistener_id, authority_id in _PUBLIC_JURISDICTIONS:
        match = pattern.search(query or "")
        if match:
            matches.append((match.start(), courtlistener_id, authority_id))
    matches.sort(key=lambda item: item[0])
    return [
        (courtlistener_id, authority_id)
        for _, courtlistener_id, authority_id in matches
    ]


def _query_mentions_any_public_jurisdiction(query: str) -> bool:
    """Detect explicit jurisdiction text even when that corpus is unsupported.

    Unsupported explicit states suppress a trusted default instead of silently
    searching the wrong state. State abbreviations are only treated as such
    when uppercase, avoiding common words such as ``in`` and ``or``.
    """
    if _explicit_public_jurisdictions(query):
        return True
    if (
        _US_STATE_NAME_RE.search(query or "")
        or _FEDERAL_JURISDICTION_RE.search(query or "")
        or _FEDERAL_JURISDICTION_ABBREVIATION_RE.search(query or "")
    ):
        return True
    return any(
        token in _US_STATE_CODES for token in re.findall(r"\b[A-Z]{2}\b", query or "")
    )


def select_public_jurisdiction_default(
    matter_jurisdiction: str | None,
    primary_jurisdictions: list[str] | tuple[str, ...] | None,
) -> str | None:
    """Select one trusted, supported default jurisdiction for public retrieval.

    Structured matter context takes precedence over the user's verified global
    profile. Ambiguous or unsupported values deliberately produce no default;
    query text remains authoritative and is evaluated separately at search time.
    """
    candidates = [matter_jurisdiction, *(primary_jurisdictions or ())]
    for candidate in candidates:
        matches = _explicit_public_jurisdictions(str(candidate or ""))
        if len(matches) == 1:
            return matches[0][1]
    return None


def infer_public_jurisdictions(query: str, tool_name: str) -> list[str]:
    """Return every explicit state using the selected MCP corpus's key format."""
    key_index = 1 if tool_name == "search_legal_authorities" else 0
    return [target[key_index] for target in _explicit_public_jurisdictions(query)]


def infer_public_jurisdiction(query: str, tool_name: str) -> str | None:
    """Return a sole explicit state while preserving the legacy helper contract."""
    jurisdictions = infer_public_jurisdictions(query, tool_name)
    return jurisdictions[0] if len(jurisdictions) == 1 else None


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
    matter_id: str | None = None,
    top_k: int = 8,
) -> list[dict]:
    """Search chunks by PostgreSQL full-text search (BM25-like).

    Uses plainto_tsquery for user-friendly search that doesn't require
    tsquery syntax. The query is normalized: punctuation stripped, lowercased,
    and OR'd together for broad recall.
    """
    terms = sorted(_retrieval_terms(query))
    if not terms:
        return []
    # websearch_to_tsquery understands explicit OR and safely parses the bound
    # value. Broad recall is paired with filter_private_retrieval_results(), so
    # a single generic legal word cannot leak an unrelated firm document.
    fts_query = " OR ".join(terms)

    sql = text("""
        SELECT
            c.id::text,
            c.content,
            c.case_name,
            c.citation,
            c.court,
            c.decision_date,
            c.chunk_index,
            c.section_path,
            c.clause_type,
            c.document_id::text AS document_id,
            d.filename AS document_title,
            ts_rank(c.fts, websearch_to_tsquery('english', :query)) AS fts_rank,
            0.0 AS similarity
        FROM chunks c
        LEFT JOIN documents d
          ON d.id = c.document_id AND d.tenant_id = c.tenant_id
        WHERE c.tenant_id = CAST(:tenant_id AS uuid)
          AND (
            c.document_id IS NULL
            OR (
              d.status = 'ready'
              AND d.conversation_id IS NULL
              AND (
                d.matter_id IS NULL
                OR (
                  CAST(:matter_id AS uuid) IS NOT NULL
                  AND d.matter_id = CAST(:matter_id AS uuid)
                )
              )
            )
          )
          AND c.fts @@ websearch_to_tsquery('english', :query)
        ORDER BY fts_rank DESC
        LIMIT :top_k
    """)

    result = await db.execute(
        sql,
        {
            "tenant_id": tenant_id,
            "matter_id": matter_id,
            "top_k": top_k,
            "query": fts_query,
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
            "document_id": row.document_id,
            "document_title": row.document_title,
            "similarity": float(row.fts_rank),
            "source": "tenant_document_fts",
        }
        for row in rows
    ]


async def search_chunks(
    db: AsyncSession,
    query_embedding: List[float],
    tenant_id: str,
    matter_id: str | None = None,
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
            c.id::text,
            c.content,
            c.case_name,
            c.citation,
            c.court,
            c.decision_date,
            c.chunk_index,
            c.section_path,
            c.clause_type,
            c.document_id::text AS document_id,
            d.filename AS document_title,
            1 - (c.embedding <=> CAST(:vec AS vector)) AS similarity
        FROM chunks c
        LEFT JOIN documents d
          ON d.id = c.document_id AND d.tenant_id = c.tenant_id
        WHERE c.tenant_id = CAST(:tenant_id AS uuid)
          AND (
            c.document_id IS NULL
            OR (
              d.status = 'ready'
              AND d.conversation_id IS NULL
              AND (
                d.matter_id IS NULL
                OR (
                  CAST(:matter_id AS uuid) IS NOT NULL
                  AND d.matter_id = CAST(:matter_id AS uuid)
                )
              )
            )
          )
          AND c.embedding IS NOT NULL
        ORDER BY c.embedding <=> CAST(:vec AS vector)
        LIMIT :top_k
    """)

    result = await db.execute(
        sql,
        {
            "tenant_id": tenant_id,
            "matter_id": matter_id,
            "top_k": top_k,
            "vec": vec_str,
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
            "document_id": row.document_id,
            "document_title": row.document_title,
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


def _score_value(value: Any) -> float:
    """Return a finite non-negative retrieval score without trusting wire types."""

    try:
        score = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return score if 0.0 <= score <= 1.0 else 0.0


def _mcp_item_to_chunk(item: dict[str, Any], rank_index: int) -> dict:
    """Map a CourtListener MCP search hit to the chat/RAG chunk contract."""
    chunk_id = str(item.get("chunk_id") or item.get("id") or f"mcp_{rank_index}")
    search_source = str(item.get("search_source") or "").casefold()
    rank_score = item.get("similarity")
    # Older MCP deployments returned only ``rank``. Preserve that contract, but
    # do not turn a current FTS rank into a fabricated 100%, 95%, ... semantic
    # score merely because the row appeared near the top of an OR query.
    if rank_score is None and not search_source:
        rank_score = item.get("rank")
    similarity = _score_value(rank_score)

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
        "relevance_score": similarity or _score_value(item.get("keyword_rank")),
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
    search_source = str(item.get("search_source") or "").casefold()
    rank_score = item.get("similarity")
    if rank_score is None and not search_source:
        rank_score = item.get("rank")
    similarity = _score_value(rank_score)
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
        "relevance_score": similarity or _score_value(item.get("keyword_rank")),
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
    jurisdiction: str | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    started_at = time.monotonic()
    outcome = {
        "tool_name": tool_name,
        "status_code": 503,
        "result_count": 0,
        "latency_ms": 0,
    }
    try:
        arguments: dict[str, Any] = {"query": query, "top_k": top_k}
        if jurisdiction:
            arguments["jurisdiction"] = jurisdiction
        response = await client.post(
            url,
            json={"name": tool_name, "arguments": arguments},
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

    def __init__(
        self,
        values: list[dict],
        outcomes: list[dict[str, Any]],
        *,
        requested_jurisdictions: tuple[str, ...] | list[str] = (),
        missing_jurisdictions: tuple[str, ...] | list[str] = (),
    ):
        super().__init__(values)
        self.mcp_outcomes = outcomes
        self.requested_jurisdictions = tuple(dict.fromkeys(requested_jurisdictions))
        self.missing_jurisdictions = tuple(dict.fromkeys(missing_jurisdictions))


class RAGChunks(list):
    """Retrieved chunks with non-serialized health metadata.

    The list behavior keeps existing API and cache serialization call sites
    compatible.  Health metadata is intentionally process-local: degraded
    results are never written to cache, while healthy cached values remain
    ordinary lists when they are read back from Redis.
    """

    def __init__(
        self,
        values: list[dict] | None = None,
        *,
        degradation_reasons: tuple[str, ...] | list[str] = (),
        requested_public_jurisdictions: tuple[str, ...] | list[str] = (),
        missing_public_jurisdictions: tuple[str, ...] | list[str] = (),
    ):
        super().__init__(values or [])
        self.degradation_reasons = tuple(dict.fromkeys(degradation_reasons))
        self.degraded = bool(self.degradation_reasons)
        self.requested_public_jurisdictions = tuple(
            dict.fromkeys(requested_public_jurisdictions)
        )
        self.missing_public_jurisdictions = tuple(
            dict.fromkeys(missing_public_jurisdictions)
        )


class ConnectedSourceResults(tuple):
    """Three-value connected result with process-local health metadata.

    Remaining tuple-compatible preserves existing callers/tests while allowing
    the hybrid result to suppress cache writes after a planner, cloud, or SMB
    outage instead of treating a partial local-only answer as fully healthy.
    """

    def __new__(
        cls,
        cloud_context: str = "",
        cloud_hits: list[dict] | None = None,
        smb_context: str = "",
        *,
        degradation_reasons: tuple[str, ...] | list[str] = (),
    ):
        value = super().__new__(cls, (cloud_context, cloud_hits or [], smb_context))
        value.degradation_reasons = tuple(dict.fromkeys(degradation_reasons))
        value.degraded = bool(value.degradation_reasons)
        return value


def rag_result_is_cacheable(
    context_str: str,
    chunks: list[dict],
    cloud_hits: list[dict] | None = None,
) -> bool:
    """Return whether a complete, non-empty retrieval result may be cached."""
    if bool(getattr(chunks, "degraded", False)):
        return False
    if bool(getattr(chunks, "missing_public_jurisdictions", ())):
        return False
    return bool((context_str or "").strip() or chunks or cloud_hits)


def _retrieval_value_or_default(
    result: Any,
    *,
    stage: str,
    default: Any,
    degradation_reasons: list[str],
) -> Any:
    """Unwrap one gather result without hiding cancellation or degradation."""
    if isinstance(result, asyncio.CancelledError):
        raise result
    if isinstance(result, BaseException):
        if not isinstance(result, Exception):
            raise result
        logger.warning("RAG stage %s failed: %s", stage, result)
        degradation_reasons.append(stage)
        return default
    return result


async def search_courtlistener_mcp(
    query: str,
    top_k: int = 8,
    default_jurisdiction: str | None = None,
) -> list[dict]:
    """Search public authority through the configured CourtListener MCP server."""
    if not settings.MCP_SERVER_URL:
        return []

    url = f"{settings.MCP_SERVER_URL.rstrip('/')}/api/mcp/tools/call"
    explicit_jurisdictions = _explicit_public_jurisdictions(query)
    default_target = select_public_jurisdiction_default(
        default_jurisdiction,
        None,
    )
    target_jurisdictions = explicit_jurisdictions
    if (
        not target_jurisdictions
        and default_target
        and not _query_mentions_any_public_jurisdiction(query)
    ):
        target_jurisdictions = _explicit_public_jurisdictions(default_target)
    requested_jurisdictions = [
        canonical_jurisdiction
        for _courtlistener_id, canonical_jurisdiction in target_jurisdictions
    ]

    def result_with_coverage(
        values: list[dict], outcomes: list[dict[str, Any]]
    ) -> MCPPublicResults:
        returned_jurisdictions = {
            str(chunk.get("retrieval_jurisdiction") or "")
            for chunk in values
            if chunk.get("retrieval_jurisdiction")
        }
        missing_jurisdictions = [
            jurisdiction
            for jurisdiction in requested_jurisdictions
            if jurisdiction not in returned_jurisdictions
        ]
        return MCPPublicResults(
            values,
            outcomes,
            requested_jurisdictions=requested_jurisdictions,
            missing_jurisdictions=missing_jurisdictions,
        )

    search_plan: list[tuple[str, str | None, str | None]] = []
    if target_jurisdictions:
        for tool_name, key_index in (
            ("search_caselaw", 0),
            ("search_legal_authorities", 1),
        ):
            search_plan.extend(
                (tool_name, jurisdiction[key_index], jurisdiction[1])
                for jurisdiction in target_jurisdictions
            )
    else:
        search_plan = [
            ("search_caselaw", None, None),
            ("search_legal_authorities", None, None),
        ]

    if not settings.MCP_UPSTREAM_API_KEY:
        logger.error("CourtListener MCP upstream authentication is not configured")
        return result_with_coverage(
            [],
            [
                {
                    "tool_name": tool_name,
                    "jurisdiction": jurisdiction,
                    "status_code": 503,
                    "result_count": 0,
                    "latency_ms": 0,
                }
                for tool_name, jurisdiction, _canonical_jurisdiction in search_plan
            ],
        )
    timeout = httpx.Timeout(12.0, connect=3.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        search_results = await asyncio.gather(
            *(
                _call_public_mcp_search(
                    client,
                    url,
                    tool_name,
                    query,
                    top_k,
                    jurisdiction,
                )
                for tool_name, jurisdiction, _canonical_jurisdiction in search_plan
            )
        )

    chunks_by_jurisdiction: dict[str | None, list[dict]] = {
        jurisdiction[1]: [] for jurisdiction in target_jurisdictions
    }
    if not target_jurisdictions:
        chunks_by_jurisdiction[None] = []
    outcomes: list[dict[str, Any]] = []
    combined: list[dict] = []
    for (
        tool_name,
        requested_jurisdiction,
        canonical_jurisdiction,
    ), (response, outcome) in zip(search_plan, search_results):
        mapper = (
            _mcp_authority_item_to_chunk
            if tool_name == "search_legal_authorities"
            else _mcp_item_to_chunk
        )
        mapped_chunks = [
            mapper(item, index)
            for index, item in enumerate(_mcp_json_items(response or {}))
            if item.get("content")
        ]
        mapped_chunks = filter_public_retrieval_results(query, mapped_chunks)
        if canonical_jurisdiction:
            for chunk in mapped_chunks:
                chunk["retrieval_jurisdiction"] = canonical_jurisdiction
        chunks_by_jurisdiction[canonical_jurisdiction].extend(mapped_chunks)
        combined.extend(mapped_chunks)
        outcome["jurisdiction"] = requested_jurisdiction
        outcome["result_count"] = len(mapped_chunks)
        outcomes.append(outcome)

    def relevance(chunk: dict) -> float:
        return float(chunk.get("relevance_score") or 0.0)

    combined.sort(key=relevance, reverse=True)
    if len(target_jurisdictions) <= 1:
        return result_with_coverage(combined[:top_k], outcomes)

    # Reserve one unique hit per explicitly named jurisdiction before using the
    # remaining slots by global score. This prevents one state's higher scores
    # from crowding the other state entirely out of an interstate-law answer.
    selected: list[dict] = []
    selected_keys: set[tuple[str, str, str]] = set()

    def identity(chunk: dict) -> tuple[str, str, str]:
        return (
            str(chunk.get("source") or ""),
            str(chunk.get("id") or ""),
            str(chunk.get("content") or ""),
        )

    for _courtlistener_id, canonical_jurisdiction in target_jurisdictions:
        candidates = sorted(
            chunks_by_jurisdiction.get(canonical_jurisdiction, []),
            key=relevance,
            reverse=True,
        )
        candidate = next(
            (item for item in candidates if identity(item) not in selected_keys),
            None,
        )
        if candidate is None or len(selected) >= top_k:
            continue
        selected.append(candidate)
        selected_keys.add(identity(candidate))

    for candidate in combined:
        if len(selected) >= top_k:
            break
        if identity(candidate) in selected_keys:
            continue
        selected.append(candidate)
        selected_keys.add(identity(candidate))

    selected.sort(key=relevance, reverse=True)
    return result_with_coverage(selected, outcomes)


async def build_rag_context(chunks: List[dict]) -> str:
    """Format retrieved chunks into a context string with citation and clause metadata."""
    requested_jurisdictions = tuple(
        getattr(chunks, "requested_public_jurisdictions", ())
    )
    missing_jurisdictions = tuple(getattr(chunks, "missing_public_jurisdictions", ()))
    coverage_notice = ""
    if missing_jurisdictions:
        coverage_notice = (
            "--- PUBLIC AUTHORITY COVERAGE NOTICE ---\n"
            f"Requested jurisdictions: {', '.join(requested_jurisdictions)}.\n"
            f"No public authority was retrieved for: {', '.join(missing_jurisdictions)}.\n"
            "The excerpts below do not establish authority for the missing "
            "jurisdiction(s). Explicitly disclose this gap and do not state or "
            "imply that the missing jurisdiction was researched or resolved."
        )
    if not chunks:
        return coverage_notice

    parts = [coverage_notice] if coverage_notice else []
    for i, chunk in enumerate(chunks, start=1):
        is_public = str(chunk.get("source") or "").casefold() in {
            "courtlistener_mcp",
            "legal_authority_mcp",
            "public_courtlistener",
        }
        source_title = (
            chunk.get("case_name")
            if is_public
            else chunk.get("document_title") or chunk.get("case_name")
        ) or ("Unidentified authority" if is_public else "Firm document")
        citation = chunk.get("citation") or ""
        court = chunk.get("court") or ""
        decision_date = chunk.get("decision_date") or ""
        content = chunk.get("content", "")
        similarity = chunk.get("similarity", 0.0)
        section_path = chunk.get("section_path") or ""
        clause_type = chunk.get("clause_type") or "general"
        source = chunk.get("source", "")
        source_url = _public_source_url(chunk)
        retrieval_jurisdiction = str(chunk.get("retrieval_jurisdiction") or "").strip()

        source_id = str(chunk.get("id") or "").strip()
        header_parts = [f"[{i}] {source_title}"]
        if source_id:
            header_parts.append(f"[source: {source_id}]")
        if citation:
            header_parts.append(f"Citation: {citation}")
        if source_url:
            header_parts.append(f"URL: {source_url}")
        if is_public and retrieval_jurisdiction:
            header_parts.append(f"Retrieval jurisdiction: {retrieval_jurisdiction}")
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
    matter_id: str | None = None,
    include_public: bool = True,
    include_private: bool = True,
    reuse_db_for_usage: bool = False,
    default_public_jurisdiction: str | None = None,
) -> Tuple[str, List[dict]]:
    """
    Hybrid RAG pipeline: optional tenant dense/FTS retrieval plus optional
    public CourtListener chunks.

    Embedding calls and FTS search run concurrently. Public authority is retrieved
    through CourtListener MCP when MCP_SERVER_URL is set; otherwise the legacy
    public_chunks/BGE path is used.
    Results are fused per-source: private dense + private FTS via RRF, then
    public chunks are appended (they live in a different embedding space).
    """
    degradation_reasons: list[str] = []
    use_mcp_public = include_public and bool(settings.MCP_SERVER_URL)
    public_task = None
    if use_mcp_public:
        # Public authority retrieval does not depend on the tenant embedding.
        # Start it immediately instead of making it wait for embedding + FTS.
        public_search_kwargs: dict[str, Any] = {
            "query": question,
            "top_k": settings.PUBLIC_RAG_TOP_K,
        }
        if default_public_jurisdiction:
            public_search_kwargs["default_jurisdiction"] = default_public_jurisdiction
        public_task = asyncio.create_task(
            search_courtlistener_mcp(**public_search_kwargs)
        )
    if include_public and not use_mcp_public and include_private:
        embedding_result, public_embedding_result, fts_result = await asyncio.gather(
            embedding_service.embed_text(question),
            embedding_service.embed_public_query(question),
            search_chunks_fts(
                db=db,
                query=question,
                tenant_id=tenant_id,
                matter_id=matter_id,
                top_k=settings.RAG_TOP_K,
            ),
            return_exceptions=True,
        )
        public_embedding = _retrieval_value_or_default(
            public_embedding_result,
            stage="public_embedding_failed",
            default=None,
            degradation_reasons=degradation_reasons,
        )
        if (
            public_embedding is None
            and "public_embedding_failed" not in degradation_reasons
        ):
            degradation_reasons.append("public_embedding_unavailable")
    elif include_public and not use_mcp_public:
        public_embedding_result = await embedding_service.embed_public_query(question)
        embedding_result, fts_result = None, []
        public_embedding = _retrieval_value_or_default(
            public_embedding_result,
            stage="public_embedding_failed",
            default=None,
            degradation_reasons=degradation_reasons,
        )
        if (
            public_embedding is None
            and "public_embedding_failed" not in degradation_reasons
        ):
            degradation_reasons.append("public_embedding_unavailable")
    elif include_private:
        try:
            embedding_result, fts_result = await asyncio.gather(
                embedding_service.embed_text(question),
                search_chunks_fts(
                    db=db,
                    query=question,
                    tenant_id=tenant_id,
                    matter_id=matter_id,
                    top_k=settings.RAG_TOP_K,
                ),
                return_exceptions=True,
            )
        except asyncio.CancelledError:
            if public_task is not None:
                public_task.cancel()
                await asyncio.gather(public_task, return_exceptions=True)
            raise
        public_embedding = None
    else:
        embedding_result, fts_result, public_embedding = None, [], None

    query_embedding = None
    if include_private:
        query_embedding = _retrieval_value_or_default(
            embedding_result,
            stage="tenant_embedding_failed",
            default=None,
            degradation_reasons=degradation_reasons,
        )
        if (
            query_embedding is None
            and "tenant_embedding_failed" not in degradation_reasons
        ):
            degradation_reasons.append("tenant_embedding_unavailable")
    fts_results = _retrieval_value_or_default(
        fts_result,
        stage="tenant_fts_failed",
        default=[],
        degradation_reasons=degradation_reasons,
    )

    # Dense + public searches in parallel
    dense_task = (
        search_chunks(
            db=db,
            query_embedding=query_embedding,
            tenant_id=tenant_id,
            matter_id=matter_id,
            top_k=settings.RAG_TOP_K,
        )
        if include_private and query_embedding is not None
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
    if public_task is not None:
        # Only dense retrieval uses Postgres in this branch; CourtListener MCP
        # is remote and can safely overlap it.
        dense_result, public_result = await asyncio.gather(
            dense_task,
            public_search,
            return_exceptions=True,
        )
    else:
        # Both searches use the supplied AsyncSession. SQLAlchemy forbids
        # overlapping statements on one session, and opening another session
        # would violate the one-connection active-chat invariant.
        try:
            dense_result = await dense_task
        except BaseException as exc:
            dense_result = exc
        try:
            public_result = await public_search
        except BaseException as exc:
            public_result = exc
    dense_chunks = _retrieval_value_or_default(
        dense_result,
        stage="tenant_dense_failed",
        default=[],
        degradation_reasons=degradation_reasons,
    )
    public_chunks = _retrieval_value_or_default(
        public_result,
        stage="public_search_failed",
        default=[],
        degradation_reasons=degradation_reasons,
    )
    if not use_mcp_public:
        public_chunks = filter_public_retrieval_results(question, public_chunks)
    mcp_outcomes = list(getattr(public_chunks, "mcp_outcomes", []))
    requested_public_jurisdictions: tuple[str, ...] = ()
    missing_public_jurisdictions: tuple[str, ...] = ()
    if use_mcp_public:
        requested_public_jurisdictions = tuple(
            getattr(public_chunks, "requested_jurisdictions", ())
            or (
                canonical_jurisdiction
                for _courtlistener_id, canonical_jurisdiction in (
                    _explicit_public_jurisdictions(question)
                )
            )
        )
        if len(requested_public_jurisdictions) > 1:
            reported_missing = tuple(
                getattr(public_chunks, "missing_jurisdictions", ())
            )
            covered_jurisdictions = {
                str(chunk.get("retrieval_jurisdiction") or "")
                for chunk in public_chunks
                if chunk.get("retrieval_jurisdiction")
            }
            missing_public_jurisdictions = reported_missing or tuple(
                jurisdiction
                for jurisdiction in requested_public_jurisdictions
                if jurisdiction not in covered_jurisdictions
            )
            if missing_public_jurisdictions:
                degradation_reasons.append("public_jurisdiction_incomplete")
    for outcome in mcp_outcomes:
        try:
            status_code = int(outcome.get("status_code") or 0)
        except (TypeError, ValueError):
            status_code = 500
        if status_code >= 400:
            degradation_reasons.append("public_search_degraded")
            break
    mcp_result_count = len(public_chunks)
    if use_mcp_public and not public_chunks:
        # A zero-result remote search is still a coverage gap. Preserve access
        # to the already-synced CourtListener bulk index whether the MCP tools
        # failed or simply found nothing for the phrasing used by the caller.
        try:
            public_embedding = await embedding_service.embed_public_query(question)
            if public_embedding is None:
                degradation_reasons.append("public_fallback_embedding_unavailable")
                public_chunks = []
            else:
                public_chunks = await search_public_chunks(
                    db=db,
                    query_embedding=public_embedding,
                    top_k=settings.PUBLIC_RAG_TOP_K,
                )
                public_chunks = filter_public_retrieval_results(question, public_chunks)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Legacy public-authority fallback failed")
            degradation_reasons.append("public_fallback_failed")
            public_chunks = []
    if use_mcp_public:

        async def _record_mcp_usage(usage_db: AsyncSession) -> None:
            outcomes = mcp_outcomes or [
                {
                    "tool_name": "search_caselaw",
                    "status_code": (
                        502 if "public_search_failed" in degradation_reasons else 200
                    ),
                    "result_count": mcp_result_count,
                    "latency_ms": None,
                }
            ]
            for outcome in outcomes:
                # Each usage write commits. The tenant GUC is transaction-
                # local, so rebind it before every subsequent RLS insert.
                await set_tenant_context(usage_db, str(tenant_id))
                await record_internal_chat_mcp_usage(
                    db=usage_db,
                    tenant_id=uuid.UUID(str(tenant_id)),
                    user_id=uuid.UUID(str(user_id)) if user_id else None,
                    tool_name=str(outcome["tool_name"]),
                    status_code=int(outcome["status_code"]),
                    result_count=int(outcome.get("result_count") or 0),
                    latency_ms=outcome.get("latency_ms"),
                )

        try:
            if reuse_db_for_usage:
                await _record_mcp_usage(db)
            else:
                async with async_session_maker() as usage_db:
                    await _record_mcp_usage(usage_db)
        except Exception:
            logger.exception("Failed to record internal CourtListener MCP usage")

    # Fuse private dense + FTS results via RRF
    fused_private = []
    if include_private:
        try:
            fused_private = filter_private_retrieval_results(
                question,
                reciprocal_rank_fusion(
                    dense_results=dense_chunks,
                    fts_results=fts_results,
                ),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Private RAG result fusion failed")
            degradation_reasons.append("tenant_fusion_failed")

    # Limit fused results and append public chunks
    chunks = RAGChunks(
        fused_private[: settings.RAG_TOP_K] + public_chunks,
        degradation_reasons=degradation_reasons,
        requested_public_jurisdictions=requested_public_jurisdictions,
        missing_public_jurisdictions=missing_public_jurisdictions,
    )

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


@asynccontextmanager
async def _connected_source_session(
    supplied_db: AsyncSession | None,
    tenant_id: str,
):
    """Reuse a caller's pinned session, or own a standalone helper session."""
    if supplied_db is not None:
        await set_tenant_context(supplied_db, str(tenant_id))
        yield supplied_db
        return
    async with async_session_maker() as helper_db:
        await set_tenant_context(helper_db, str(tenant_id))
        yield helper_db


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
    db: AsyncSession | None = None,
) -> tuple[str, list[dict], str]:
    """Search connected cloud/SMB sources using one supplied or helper session.

    This path is additive. It must not delay or break local/public authority
    retrieval when no provider is connected, the planner times out, or an
    upstream provider is unavailable.
    """
    if not cloud_search_service or not retrieval_planner:
        return ConnectedSourceResults()

    cloud_hits: list[dict] = []
    cloud_context = ""
    smb_context = ""
    degradation_reasons: list[str] = []
    async with _connected_source_session(db, tenant_id) as cloud_db:
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
            return ConnectedSourceResults()

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
            return ConnectedSourceResults(
                degradation_reasons=["connected_planner_timeout"]
            )
        except Exception:
            logger.exception("Connected-source retrieval planner failed")
            return ConnectedSourceResults(
                degradation_reasons=["connected_planner_failed"]
            )

        if not plan or not plan.get("should_search"):
            return ConnectedSourceResults()
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
                    degradation_reasons.append("connected_cloud_failed")

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
                degradation_reasons.append("smb_search_failed")

    return ConnectedSourceResults(
        cloud_context,
        cloud_hits,
        smb_context,
        degradation_reasons=degradation_reasons,
    )


async def hybrid_rag_query(
    db: AsyncSession,
    embedding_service: EmbeddingService,
    question: str,
    tenant_id: str,
    user_id: str | None = None,
    include_public: bool = True,
    include_private: bool = True,
    cloud_search_service=None,  # CloudSearchService | None
    retrieval_planner=None,  # RetrievalPlanner | None
    tenant_name: str = "Legal",
    matter_context_str: str | None = None,
    matter_id: str | None = None,
    matter_cloud_folder: dict | None = None,
    default_public_jurisdiction: str | None = None,
) -> tuple[str, list[dict], list[dict]]:
    """
    Hybrid RAG pipeline: optional tenant pgvector/cloud/SMB search plus public
    legal-authority retrieval.

    Uses one caller-supplied DB session. Local/public retrieval runs first;
    connected cloud and SMB retrieval follows so database statements never
    overlap or borrow another pool connection during an active chat turn.

    Returns (context_string, chunks_list, cloud_hits_list).
    The caller is responsible for merging context strings if both return results.
    """
    try:
        local_result = await full_rag_query(
            db=db,
            embedding_service=embedding_service,
            question=question,
            tenant_id=tenant_id,
            matter_id=matter_id,
            user_id=user_id,
            include_public=include_public,
            include_private=include_private,
            reuse_db_for_usage=True,
            default_public_jurisdiction=default_public_jurisdiction,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        local_result = exc

    if include_private:
        try:
            connected_result = await _connected_source_query(
                question=question,
                tenant_id=tenant_id,
                user_id=user_id,
                cloud_search_service=cloud_search_service,
                retrieval_planner=retrieval_planner,
                tenant_name=tenant_name,
                matter_context_str=matter_context_str,
                matter_id=matter_id,
                matter_cloud_folder=matter_cloud_folder,
                db=db,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            connected_result = exc
    else:
        connected_result = ("", [], "")

    if isinstance(local_result, asyncio.CancelledError):
        raise local_result
    if isinstance(local_result, BaseException):
        if not isinstance(local_result, Exception):
            raise local_result
        logger.error("Private/public RAG retrieval failed: %s", local_result)
        pgvector_context, chunks = (
            "",
            RAGChunks(degradation_reasons=["local_rag_failed"]),
        )
    else:
        pgvector_context, chunks = local_result
        if not isinstance(chunks, RAGChunks):
            chunks = RAGChunks(chunks)

    if isinstance(connected_result, asyncio.CancelledError):
        raise connected_result
    if isinstance(connected_result, BaseException):
        if not isinstance(connected_result, Exception):
            raise connected_result
        logger.error("Connected-source retrieval failed: %s", connected_result)
        cloud_context, cloud_hits, smb_context = "", [], ""
        chunks = RAGChunks(
            chunks,
            degradation_reasons=[
                *getattr(chunks, "degradation_reasons", ()),
                "connected_source_failed",
            ],
            requested_public_jurisdictions=getattr(
                chunks, "requested_public_jurisdictions", ()
            ),
            missing_public_jurisdictions=getattr(
                chunks, "missing_public_jurisdictions", ()
            ),
        )
    else:
        cloud_context, cloud_hits, smb_context = connected_result
        connected_degradation = tuple(
            getattr(connected_result, "degradation_reasons", ())
        )
        if connected_degradation:
            chunks = RAGChunks(
                chunks,
                degradation_reasons=[
                    *getattr(chunks, "degradation_reasons", ()),
                    *connected_degradation,
                ],
                requested_public_jurisdictions=getattr(
                    chunks, "requested_public_jurisdictions", ()
                ),
                missing_public_jurisdictions=getattr(
                    chunks, "missing_public_jurisdictions", ()
                ),
            )

    # 2. Merge contexts — cloud and SMB results after pgvector
    parts = [pgvector_context] if pgvector_context else []
    if cloud_context:
        parts.append(f"--- Cloud Search Results ---\n\n{cloud_context}")
    if smb_context:
        parts.append(f"--- On-Prem File Share Results ---\n\n{smb_context}")
    context_str = "\n\n".join(parts)

    await set_tenant_context(db, str(tenant_id))
    return context_str, chunks, cloud_hits
