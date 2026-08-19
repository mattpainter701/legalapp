import asyncio
import hashlib
import html
import json
import logging
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import List

import aiofiles
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import StreamingResponse
from sqlalchemy import exists, select, func, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.config import get_settings
from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.models.conversation import Conversation, Message, UsageRecord
from app.models.document import Document
from app.models.chat_artifact import ChatArtifact
from app.models.plugin import Matter as MatterModel
from app.models.tenant import Tenant
from app.schemas.chat import (
    ChatAttachmentResponse,
    ConversationCreate,
    ConversationUpdate,
    ConversationResponse,
    ConversationDetail,
    MessageCreate,
    MessageResponse,
    SourceCitation,
)
from app.services.artifact_extraction import extract_artifacts, strip_artifacts
from app.services.embeddings import EmbeddingService
from app.services.rag import (
    cloud_context_source_id,
    hybrid_rag_query,
    rag_result_is_cacheable,
    select_public_jurisdiction_default,
)
from app.services.llm import LLMService
from app.services.llm_routing import (
    classify_query_complexity,
    resolve_llm_route,
)
from app.services.billing import calculate_cost
from app.services.demo_access import reject_demo_premium
from app.services.memory_service import MemoryService
from app.services.user_context import build_global_user_context
from app.services.matter_context import MatterContextService
from app.services.cache import ExpertiseCacheManager
from app.services.gateway_privacy import gateway_metadata, retained_gateway_query_text
from app.services.chat_agent import ChatActionAgent
from app.utils.guardrails import (
    apply_guardrails,
    build_citation_annotations,
    check_pii_in_input,
    consolidate_unverified_model_knowledge,
    enforce_legal_citation_integrity,
    prepare_provider_messages,
    prepare_provider_text,
    reconcile_retrieved_source_attribution,
    validate_citation_confidence,
)
from app.services.error_tracker import capture_chat_error
from app.services.usage_limits import check_token_budget

logger = logging.getLogger(__name__)

settings = get_settings()

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_ANCHOR_TEXT_RE = re.compile(r"<a\b[^>]*>(.*?)</a>", re.IGNORECASE | re.DOTALL)
_HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)
_MARKDOWN_LINK_RE = re.compile(r"\[[^\]]*\]\(\s*([^\s)]+)\s*\)")
_SOURCE_REFERENCE_RE = re.compile(r"\[source:\s*([^\]]+)\]", re.IGNORECASE)
_UNKNOWN_SOURCE_TEXT = {
    "unknown",
    "unknown case",
    "no citation",
    "none",
    "null",
    "n/a",
}

_STREAM_INTERRUPTED_MESSAGE = (
    "**Response interrupted.** A complete answer was not saved. "
    "Retry this message before relying on the analysis."
)
_INTERNAL_DOCUMENT_URL_RE = re.compile(
    r"^/api/documents/[0-9a-fA-F-]{36}/download$"
)


def _clean_source_text(value, max_length: int | None = None) -> str:
    """Normalize CourtListener/html source fragments for API display."""
    if value is None:
        return ""
    text = str(value)
    anchor_match = _ANCHOR_TEXT_RE.search(text)
    if anchor_match:
        text = anchor_match.group(1)
    text = _HTML_TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    if max_length and len(text) > max_length:
        return text[: max_length - 1].rstrip() + "…"
    return text


def _meaningful_source_text(value, max_length: int | None = None) -> str:
    """Normalize source identity while suppressing legacy placeholder labels."""
    text = _clean_source_text(value, max_length)
    return "" if text.casefold() in _UNKNOWN_SOURCE_TEXT else text


def _first_meaningful_source_text(*values, max_length: int | None = None) -> str:
    for value in values:
        text = _meaningful_source_text(value, max_length)
        if text:
            return text
    return ""


def _normalize_source_url(value: str | None) -> str | None:
    if not value:
        return None
    url = html.unescape(str(value).strip())
    if not url:
        return None
    markdown_match = _MARKDOWN_LINK_RE.fullmatch(url)
    if markdown_match:
        url = markdown_match.group(1).strip()
    url = url.strip("<>")
    if url.startswith("//"):
        return f"https:{url}"
    # Authenticated LawHand document links must remain origin-relative so they
    # work in every deployment. CourtListener paths use other namespaces such
    # as /opinion/ and are normalized below.
    if _INTERNAL_DOCUMENT_URL_RE.fullmatch(url):
        return url
    if url.startswith("/api/"):
        return None
    if url.startswith("/"):
        return f"https://www.courtlistener.com{url}"
    if url.startswith("www.courtlistener.com/") or url.startswith("courtlistener.com/"):
        return f"https://{url}"
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return None


def _source_url_from_chunk(chunk: dict) -> str | None:
    # CourtListener opinion IDs are stable and first-party. Prefer that link
    # over a legacy publisher URL retained in older bulk-sync metadata.
    opinion_id = chunk.get("opinion_id")
    if opinion_id:
        return f"https://www.courtlistener.com/opinion/{opinion_id}/"
    for key in ("url", "source_url", "absolute_url"):
        url = _normalize_source_url(chunk.get(key))
        if url:
            return url
    document_id = chunk.get("document_id")
    if document_id:
        return f"/api/documents/{document_id}/download"
    for key in ("citation", "content", "excerpt"):
        raw = chunk.get(key)
        if not raw:
            continue
        href_match = _HREF_RE.search(str(raw))
        if href_match:
            url = _normalize_source_url(href_match.group(1))
            if url:
                return url
        markdown_match = _MARKDOWN_LINK_RE.search(str(raw))
        if markdown_match:
            url = _normalize_source_url(markdown_match.group(1))
            if url:
                return url
    return None


def _source_label(source_type: str | None) -> str:
    labels = {
        "public_authority": "Public authority",
        "courtlistener_mcp": "Public authority",
        "cloud": "Cloud context",
        "matter_context": "Matter context",
        "tenant_document": "Firm context",
    }
    return labels.get(source_type or "", "Context")


def _source_locator_from_chunk(chunk: dict) -> str | None:
    """Build an honest pinpoint label from retrieval metadata.

    This deliberately says "passage" for chunk ordinals. A retrieval chunk is
    not a legal page/line cite, so we never present it as one.
    """
    metadata = chunk.get("metadata") if isinstance(chunk.get("metadata"), dict) else {}
    parts: list[str] = []
    section = chunk.get("section_path") or metadata.get("section_path")
    if section and str(section).strip() not in {"CourtListener", "general"}:
        parts.append(str(section).strip())
    page = (
        chunk.get("page_number") or metadata.get("page_number") or metadata.get("page")
    )
    if page is not None:
        parts.append(f"Page {page}")
    paragraph = chunk.get("paragraph_number") or metadata.get("paragraph_number")
    if paragraph is not None:
        parts.append(f"Paragraph {paragraph}")
    line_start = chunk.get("line_start") or metadata.get("line_start")
    line_end = chunk.get("line_end") or metadata.get("line_end")
    if line_start is not None:
        parts.append(
            f"Lines {line_start}-{line_end}"
            if line_end is not None and str(line_end) != str(line_start)
            else f"Line {line_start}"
        )
    if not parts and chunk.get("chunk_index") is not None:
        try:
            passage = int(chunk.get("chunk_index")) + 1
        except (TypeError, ValueError):
            passage = chunk.get("chunk_index")
        parts.append(f"Retrieved passage {passage}")
    return " · ".join(parts) or None


def _source_dict_from_chunk(chunk: dict) -> dict:
    raw_source = str(chunk.get("source") or "")
    if raw_source in {
        "courtlistener_mcp",
        "legal_authority_mcp",
        "public_courtlistener",
    }:
        source_type = "public_authority"
    elif raw_source.startswith("tenant_document"):
        source_type = "tenant_document"
    elif raw_source in {"matter_context", "cloud"}:
        source_type = raw_source
    else:
        source_type = str(chunk.get("source_type") or raw_source or "context")

    is_public = source_type == "public_authority"
    if is_public:
        title = _first_meaningful_source_text(
            chunk.get("case_name"),
            chunk.get("title"),
            max_length=180,
        )
    else:
        title = _first_meaningful_source_text(
            chunk.get("document_title"),
            chunk.get("filename"),
            chunk.get("case_name"),
            chunk.get("title"),
            max_length=180,
        )
    title = title or ("Unidentified authority" if is_public else "Firm document")
    citation = _first_meaningful_source_text(
        chunk.get("citation"),
        chunk.get("document_title") if not is_public else None,
        max_length=120,
    )
    source_dict = {
        "source_id": str(chunk.get("id")) if chunk.get("id") is not None else None,
        "case_name": title,
        "citation": citation,
        "court": _clean_source_text(chunk.get("court"), 120) or None,
        "excerpt": _clean_source_text(
            chunk.get("content") or chunk.get("excerpt"), 300
        ),
        "url": _source_url_from_chunk(chunk),
        "source_type": source_type,
        "source_label": _source_label(source_type),
        "locator": _source_locator_from_chunk(chunk),
        "relevance_score": (
            chunk.get("evidence_relevance_score")
            if is_public
            else chunk.get("similarity")
        ),
        "authority_tier": _clean_source_text(chunk.get("authority_tier"), 80) or None,
        "official_status": _clean_source_text(chunk.get("official_status"), 40) or None,
        "effective_date": _clean_source_text(chunk.get("effective_date"), 40) or None,
        "cited": False,
    }
    retrieval_jurisdiction = _clean_source_text(chunk.get("retrieval_jurisdiction"), 20)
    if is_public and retrieval_jurisdiction:
        source_dict["retrieval_jurisdiction"] = retrieval_jurisdiction
    return source_dict


def _mark_cited_sources(text: str, sources: list[dict]) -> list[dict]:
    """Record which retrieved rows the final answer actually references."""
    cited_ids = {
        value.strip().casefold() for value in _SOURCE_REFERENCE_RE.findall(text or "")
    }
    return [
        {
            **source,
            "cited": bool(
                source.get("source_id")
                and str(source["source_id"]).strip().casefold() in cited_ids
            ),
        }
        for source in sources
    ]


def _source_dict_from_cloud_hit(hit_dict: dict) -> dict:
    source_type = "cloud"
    cloud_id = _cloud_hit_context_id(hit_dict)
    return {
        "source_id": cloud_id,
        "case_name": _clean_source_text(hit_dict.get("title"), 180) or "Cloud result",
        "citation": _clean_source_text(hit_dict.get("url") or cloud_id, 140),
        "court": _clean_source_text(
            f"{hit_dict.get('provider', 'cloud')}/{hit_dict.get('source', 'unknown')}",
            120,
        ),
        "excerpt": _clean_source_text(hit_dict.get("snippet"), 300),
        "url": _normalize_source_url(hit_dict.get("url")),
        "source_type": source_type,
        "source_label": _source_label(source_type),
        "locator": _clean_source_text(
            hit_dict.get("section_path") or hit_dict.get("modified_time"), 120
        )
        or None,
        "relevance_score": hit_dict.get("relevance_score"),
    }


def _canonicalize_source_references(text: str, aliases: dict[str, str]) -> str:
    """Point duplicate chunk ids at the single source retained for display."""
    if not text or not aliases:
        return text
    normalized_aliases = {
        str(source_id).strip().casefold(): str(canonical_id).strip()
        for source_id, canonical_id in aliases.items()
        if source_id and canonical_id
    }

    def replace(match: re.Match) -> str:
        source_id = match.group(1).strip()
        canonical_id = normalized_aliases.get(source_id.casefold())
        return f"[source: {canonical_id}]" if canonical_id else match.group(0)

    return _SOURCE_REFERENCE_RE.sub(replace, text)


def _citation_validation_sources(
    source_dicts: list[dict], chunks: list[dict], cloud_hits: list
) -> list[dict]:
    """Pair public API citations with the full excerpts shown to the model."""
    rows = list(source_dicts)
    rows.extend(
        {
            "source_id": str(chunk.get("id")),
            "content": chunk.get("content") or chunk.get("excerpt") or "",
        }
        for chunk in chunks
        if chunk.get("id")
    )
    for hit in cloud_hits:
        hit_dict = _cloud_hit_dict(hit)
        rows.append(
            {
                "source_id": _cloud_hit_context_id(hit_dict),
                "content": hit_dict.get("_validation_content")
                or hit_dict.get("snippet")
                or "",
            }
        )
    return rows


async def _safe_cache_op(db, user, request, conv_id, query_text, op_name, coro):
    """Execute a cache operation with error capture on failure (non-fatal)."""
    try:
        return await coro()
    except Exception as cache_exc:
        logger.warning(f"Cache operation failed ({op_name}): {cache_exc}")
        await capture_chat_error(
            db=db,
            error_type="cache_error",
            message=f"Cache {op_name} failed: {cache_exc}",
            user_id=user.id,
            tenant_id=user.tenant_id,
            request=request,
            query_text=query_text,
            conversation_id=conv_id,
            severity="warning",
        )
        return None


router = APIRouter(prefix="/conversations", tags=["chat"])

embedding_service = EmbeddingService()
llm_service = LLMService()
chat_action_agent = ChatActionAgent(llm_service)
memory_service = MemoryService(llm_service)
matter_context_service = MatterContextService()
cache_manager = ExpertiseCacheManager()

# Cloud search (lazy-init — only used when CLOUD_SEARCH_ENABLED and tenant has integrations)
_cloud_search_service = None
_retrieval_planner = None


def _get_cloud_search_service():
    global _cloud_search_service
    if _cloud_search_service is None:
        from app.services.cloud_search import CloudSearchService

        _cloud_search_service = CloudSearchService()
    return _cloud_search_service


def _get_retrieval_planner():
    global _retrieval_planner
    if _retrieval_planner is None:
        from app.services.retrieval_planner import RetrievalPlanner

        _retrieval_planner = RetrievalPlanner(llm_service)
    return _retrieval_planner


def _join_context_sections(*sections: str | None) -> str:
    """Join non-empty prompt context sections without stray separators."""
    return "\n\n".join(
        section.strip() for section in sections if section and section.strip()
    )


def _is_public_authority_chunk(chunk: dict) -> bool:
    return (
        chunk.get("source")
        in {"courtlistener_mcp", "legal_authority_mcp", "public_courtlistener"}
        or chunk.get("source_type") == "public_authority"
        or chunk.get("clause_type") == "public_authority"
        or chunk.get("source_label") in {"Cited authority", "Public authority"}
    )


def _stream_source_counts(
    *,
    chunks: list[dict] | None,
    cloud_hits: list | None,
    has_matter_context: bool,
    attachment_count: int,
) -> dict:
    public_count = 0
    firm_chunk_count = 0
    for chunk in chunks or []:
        if _is_public_authority_chunk(chunk):
            public_count += 1
        else:
            firm_chunk_count += 1

    counts = {
        "matter": 1 if has_matter_context else 0,
        "uploads": max(0, int(attachment_count or 0)),
        "firm": firm_chunk_count + len(cloud_hits or []),
        "courtlistener": public_count,
    }
    counts["total"] = sum(counts.values())
    return counts


def _stream_progress_event(event: str, payload: dict | None = None) -> str:
    data = {"type": "progress", "event": event, **(payload or {})}
    return f"data: [PROGRESS]{json.dumps(data)}\n\n"


def _stream_token_event(content: str) -> str:
    """Encode answer text as JSON so SSE preserves Markdown newlines exactly."""
    return f"data: [TOKEN]{json.dumps(content)}\n\n"


def _stream_error_event(message: str) -> str:
    """Emit the single error sentinel understood by every chat stream client."""
    safe_message = re.sub(r"\s+", " ", str(message or "")).strip()
    if not safe_message:
        safe_message = "Assistant service temporarily unavailable. Retry this message."
    return f"data: [ERROR] {safe_message}\n\n"


def _stream_artifacts_event(artifacts: list[ChatArtifact]) -> str:
    """Notify the client of document artifacts created during this message."""
    payload = {"type": "artifacts", "artifacts": _artifact_summaries(artifacts)}
    return f"data: [ARTIFACTS]{json.dumps(payload)}\n\n"


def _stream_activity_event(
    activity_id: str,
    state: str,
    label: str,
    *,
    elapsed_ms: int | None = None,
    counts: dict | None = None,
    sources: list[dict] | None = None,
    detail: str | None = None,
) -> str:
    activity = {
        "id": activity_id,
        "state": state,
        "label": label,
    }
    if elapsed_ms is not None:
        activity["elapsed_ms"] = max(0, int(elapsed_ms))
    if counts is not None:
        activity["counts"] = counts
    if sources:
        activity["sources"] = sources
    if detail:
        activity["detail"] = detail
    return _stream_progress_event(
        "activity",
        {
            "activity": activity,
            "status": label,
            **({"counts": counts} if counts is not None else {}),
        },
    )


def _stream_source_previews(chunks: list[dict], cloud_hits: list) -> list[dict]:
    firm_previews: list[dict] = []
    authority_previews: list[dict] = []
    for chunk in chunks:
        source = _source_dict_from_chunk(chunk)
        preview = {
            key: source.get(key)
            for key in (
                "source_id",
                "case_name",
                "citation",
                "source_label",
                "source_type",
                "locator",
                "retrieval_jurisdiction",
                "url",
            )
            if source.get(key)
        }
        destination = (
            authority_previews
            if source.get("source_type") == "public_authority"
            else firm_previews
        )
        if len(destination) < 2:
            destination.append(preview)
    for hit in cloud_hits:
        source = _source_dict_from_cloud_hit(_cloud_hit_dict(hit))
        if len(firm_previews) >= 2:
            break
        firm_previews.append(
            {
                key: source.get(key)
                for key in (
                    "source_id",
                    "case_name",
                    "citation",
                    "source_label",
                    "source_type",
                    "locator",
                    "retrieval_jurisdiction",
                    "url",
                )
                if source.get(key)
            }
        )
    return firm_previews + authority_previews


def _partition_stream_source_previews(
    source_previews: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Split preview rows by stable provenance, not a user-facing label."""
    authority = [
        source
        for source in source_previews
        if source.get("source_type") == "public_authority"
        or source.get("source_label") in {"Cited authority", "Public authority"}
    ]
    authority_ids = {id(source) for source in authority}
    firm = [source for source in source_previews if id(source) not in authority_ids]
    return firm, authority


def _auto_tier(query: str, user_requested_premium: bool) -> bool:
    """Determine whether to use the premium tier based on query complexity.

    - Explicit premium requests stay premium.
    - Complex queries (drafting, analysis, multi-hop) → premium (even if user
      didn't request it — better quality for hard questions).
    - Everything else → standard.
    """
    if user_requested_premium:
        return True
    complexity = classify_query_complexity(query)
    if complexity == "complex":
        return True  # auto-upgrade to premium
    return False


def _premium_for_user(user, query: str, user_requested_premium: bool) -> bool:
    """Apply per-user premium assignment after route classification."""
    return bool(
        getattr(user, "premium_ai_enabled", False)
        and _auto_tier(query, user_requested_premium)
    )


_PUBLIC_GENERAL_MATTER_DETAIL = (
    "This conversation is linked to a matter. Standard AI cannot use matter "
    "context; start an unlinked general conversation or use an approved private route."
)
_PUBLIC_GENERAL_ATTACHMENT_DETAIL = (
    "Standard AI cannot process attachments. Start an unlinked general "
    "conversation or use an approved private route."
)


def _is_public_general_route(route) -> bool:
    """Return whether this request must carry only public/general data.

    The route *request* is decisive, not the configured model alias. That
    keeps a tenant's managed-standard alias or a future provider change from
    accidentally receiving client context.
    """
    return route.requested_route in {"standard", "tenant-standard"}


def _assert_public_general_sources_allowed(
    conv: Conversation,
    body: MessageCreate,
) -> None:
    """Block sources that cannot safely cross the Standard provider boundary."""
    if conv.matter_id or getattr(body, "matter_id", None):
        raise HTTPException(status_code=409, detail=_PUBLIC_GENERAL_MATTER_DETAIL)
    if getattr(body, "attachment_ids", None):
        raise HTTPException(status_code=409, detail=_PUBLIC_GENERAL_ATTACHMENT_DETAIL)


async def _matter_for_tenant_or_400(
    db: AsyncSession,
    user,
    matter_id: str | uuid.UUID,
) -> MatterModel:
    try:
        matter_uuid = (
            matter_id
            if isinstance(matter_id, uuid.UUID)
            else uuid.UUID(str(matter_id).strip())
        )
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Matter ID is invalid")

    result = await db.execute(
        select(MatterModel).where(
            MatterModel.id == matter_uuid,
            MatterModel.tenant_id == user.tenant_id,
        )
    )
    matter = result.scalar_one_or_none()
    if matter is None:
        raise HTTPException(status_code=400, detail="Matter not found")
    return matter


async def _conversation_has_history(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> bool:
    result = await db.execute(
        select(
            exists().where(
                Message.conversation_id == conversation_id,
                Message.tenant_id == tenant_id,
            ),
            exists().where(
                Document.conversation_id == conversation_id,
                Document.tenant_id == tenant_id,
            ),
        )
    )
    has_messages, has_attachments = result.one()
    return bool(has_messages or has_attachments)


async def _effective_message_matter(
    db: AsyncSession,
    user,
    conv: Conversation,
    body: MessageCreate,
) -> MatterModel | None:
    requested = body.matter_id.strip() if getattr(body, "matter_id", None) else ""
    if requested:
        matter = await _matter_for_tenant_or_400(db, user, requested)
        if conv.matter_id and matter.id != conv.matter_id:
            raise HTTPException(
                status_code=409,
                detail=_MATTER_RELINK_FORBIDDEN_DETAIL,
            )
        if conv.matter_id is None:
            if await _conversation_has_history(db, conv.id, user.tenant_id):
                raise HTTPException(
                    status_code=409,
                    detail=_MATTER_RELINK_FORBIDDEN_DETAIL,
                )
            conv.matter_id = matter.id
        return matter

    if conv.matter_id:
        return await _matter_for_tenant_or_400(db, user, conv.matter_id)
    return None


def _rag_scope_key(
    matter_id: str | None,
    matter_cloud_folder: dict | None,
    default_public_jurisdiction: str | None = None,
) -> str:
    return json.dumps(
        {
            "matter_id": matter_id or "none",
            "matter_cloud_folder": matter_cloud_folder or None,
            "default_public_jurisdiction": default_public_jurisdiction or None,
        },
        sort_keys=True,
        default=str,
    )


def _cloud_hit_dict(hit) -> dict:
    return hit.to_dict() if hasattr(hit, "to_dict") else dict(hit)


def _cloud_hit_context_id(hit_dict: dict) -> str:
    return cloud_context_source_id(hit_dict)


async def _build_attachment_context(
    db: AsyncSession,
    user,
    conversation: Conversation,
    attachment_ids: list[str] | None,
) -> tuple[str, list[dict]]:
    """Inject session-attachment text directly into context (Tier 1 — no embeddings).

    For documents that were chunked/embedded (chunk_count > 0), use the stored
    chunks. Otherwise extract text on-demand from the file on disk.
    """
    if not attachment_ids:
        return "", []
    try:
        from app.utils.text_processing import extract_text as _extract_text

        attachment_parts = []
        attachment_sources: list[dict] = []
        for aid in attachment_ids:
            doc_result = await db.execute(
                select(Document).where(
                    Document.id == aid,
                    Document.tenant_id == user.tenant_id,
                    Document.conversation_id == conversation.id,
                )
            )
            doc = doc_result.scalar_one_or_none()
            if doc is None:
                continue

            text = ""
            if doc.chunk_count and doc.chunk_count > 0:
                from sqlalchemy import text as _sa_text

                chunk_result = await db.execute(
                    _sa_text(
                        "SELECT content FROM chunks WHERE document_id = CAST(:doc_id AS uuid) "
                        "AND tenant_id = CAST(:tenant_id AS uuid) "
                        "ORDER BY chunk_index LIMIT 100"
                    ),
                    {"doc_id": str(doc.id), "tenant_id": str(user.tenant_id)},
                )
                text = "\n\n".join(row[0] for row in chunk_result.fetchall())
            elif doc.storage_path and os.path.exists(doc.storage_path):
                async with aiofiles.open(doc.storage_path, "rb") as f:
                    file_bytes = await f.read()
                text = await asyncio.to_thread(
                    _extract_text,
                    file_bytes=file_bytes,
                    content_type=doc.content_type or "",
                    filename=doc.filename or "",
                )

            if text:
                source_id = f"document:{doc.id}"
                source_url = f"/api/documents/{doc.id}/download"
                attachment_parts.append(
                    "\n".join(
                        [
                            f"[Attachment: {doc.filename or 'Untitled'}]",
                            f"Source ID: {source_id}",
                            f"URL: {source_url}",
                            (
                                "Citation instruction: cite every factual finding from "
                                f"this file with [source: {source_id}] and state the "
                                "section, page, paragraph, or schedule row in the finding."
                            ),
                            text[:4000],
                        ]
                    )
                )
                attachment_sources.append(
                    {
                        "source_id": source_id,
                        "case_name": doc.filename or "Untitled attachment",
                        "citation": doc.filename or "Attached document",
                        "court": "Uploaded attachment",
                        "excerpt": _clean_source_text(text, 300),
                        "url": source_url,
                        "source_type": "tenant_document",
                        "source_label": "Attached document",
                        "locator": "Full attached document",
                    }
                )

        if attachment_parts:
            return (
                (
                    "--- Attached Files (session-only, not saved to project) ---\n\n"
                    + "\n\n---\n\n".join(attachment_parts)
                ),
                attachment_sources,
            )
    except Exception:
        logger.warning("Failed to build attachment context", exc_info=True)
    return "", []


def _conversation_to_response(
    conv: Conversation,
    message_count: int = None,
    attachment_count: int = 0,
) -> ConversationResponse:
    return ConversationResponse(
        id=str(conv.id),
        title=conv.title,
        matter_id=str(conv.matter_id) if conv.matter_id else None,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        message_count=message_count,
        attachment_count=max(0, int(attachment_count or 0)),
    )


def _stored_source_type(source: dict) -> str:
    """Recover honest provenance for legacy source rows with sparse metadata."""
    explicit = str(source.get("source_type") or source.get("source") or "").strip()
    if explicit in {
        "public_authority",
        "courtlistener_mcp",
        "legal_authority_mcp",
        "public_courtlistener",
    }:
        return "public_authority"
    if explicit.startswith("tenant_document"):
        return "tenant_document"
    if explicit in {"cloud", "matter_context"}:
        return explicit

    source_id = str(source.get("source_id") or source.get("id") or "").casefold()
    if source_id.startswith(("courtlistener:", "authority:")):
        return "public_authority"
    if source_id.startswith(("document:", "tenant:")):
        return "tenant_document"
    if source_id.startswith("cloud:"):
        return "cloud"

    source_url = _source_url_from_chunk(source) or ""
    if "courtlistener.com/" in source_url.casefold():
        return "public_authority"
    if _meaningful_source_text(source.get("court"), 120):
        return "public_authority"
    return "context"


async def _persist_message_artifacts(
    db: AsyncSession,
    *,
    user,
    conv: Conversation,
    assistant_msg: Message,
    response_text: str,
) -> tuple[str, list[ChatArtifact]]:
    """Extract artifact blocks from an assistant response and persist them.

    Returns (visible_content, artifacts). Artifact blocks are stripped from the
    message body shown in chat; the document content lives on the artifact rows.
    Extraction failures must never break the chat flow.
    """
    try:
        extracted = extract_artifacts(response_text)
    except Exception:
        logger.warning("Artifact extraction failed", exc_info=True)
        return response_text, []

    if not extracted:
        return response_text, []

    visible_content = strip_artifacts(response_text)
    artifacts: list[ChatArtifact] = []
    try:
        async with db.begin_nested():
            for item in extracted:
                artifact = ChatArtifact(
                    id=uuid.uuid4(),
                    tenant_id=user.tenant_id,
                    conversation_id=conv.id,
                    message_id=assistant_msg.id,
                    created_by_user_id=user.id,
                    title=item.title,
                    content=item.content,
                    format="markdown",
                    matter_id=conv.matter_id,
                )
                db.add(artifact)
                artifacts.append(artifact)
            await db.flush()
    except Exception:
        logger.warning("Artifact persistence failed", exc_info=True)
        return response_text, []
    return visible_content, artifacts


def _artifact_summaries(artifacts: list[ChatArtifact]) -> list[dict]:
    return [
        {
            "id": str(a.id),
            "title": a.title,
            "format": a.format,
            "version": a.version,
            "saved_to_matter": a.saved_to_matter,
        }
        for a in artifacts
    ]


def _message_to_response(
    msg: Message, artifacts: list[ChatArtifact] | None = None
) -> MessageResponse:
    sources = []
    if msg.sources:
        for s in msg.sources:
            source_type = _stored_source_type(s)
            if source_type == "public_authority":
                fallback_name = "Unidentified authority"
            elif source_type == "tenant_document":
                fallback_name = "Firm document"
            elif source_type == "cloud":
                fallback_name = "Cloud result"
            elif source_type == "matter_context":
                fallback_name = "Matter context"
            else:
                fallback_name = "Retrieved context"
            source_name = (
                _first_meaningful_source_text(
                    s.get("document_title"),
                    s.get("case_name"),
                    s.get("title"),
                    max_length=180,
                )
                or fallback_name
            )
            sources.append(
                SourceCitation(
                    source_id=s.get("source_id") or s.get("id"),
                    case_name=source_name,
                    citation=_meaningful_source_text(s.get("citation"), 120),
                    court=_clean_source_text(s.get("court"), 120) or None,
                    excerpt=_clean_source_text(s.get("excerpt"), 300),
                    url=_source_url_from_chunk(s),
                    source_type=source_type,
                    source_label=(
                        _meaningful_source_text(s.get("source_label"), 80)
                        or _source_label(source_type)
                    ),
                    locator=_clean_source_text(s.get("locator"), 160) or None,
                    retrieval_jurisdiction=(
                        _clean_source_text(s.get("retrieval_jurisdiction"), 20) or None
                    ),
                    relevance_score=s.get("relevance_score"),
                    authority_tier=(
                        _clean_source_text(s.get("authority_tier"), 80) or None
                    ),
                    official_status=(
                        _clean_source_text(s.get("official_status"), 40) or None
                    ),
                    effective_date=(
                        _clean_source_text(s.get("effective_date"), 40) or None
                    ),
                    cited=bool(s.get("cited")),
                )
            )
    return MessageResponse(
        id=str(msg.id),
        conversation_id=str(msg.conversation_id),
        role=msg.role,
        content=msg.content,
        sources=sources,
        citation_annotations=build_citation_annotations(
            msg.content,
            [source.model_dump() for source in sources],
        ),
        proposed_actions=list(msg.proposed_actions or []),
        artifacts=_artifact_summaries(artifacts or []),
        created_at=msg.created_at,
    )


def _conversation_belongs_to_user(conv: Conversation, user) -> bool:
    """Chat conversations are private to their creator, including tenant admins."""
    return str(conv.user_id) == str(user.id)


def _record_action_usage(
    db, user, *, question, route, outcome, conversation_id=None
) -> None:
    """Bill the chat action pass like any other completion."""
    if outcome.tokens_in or outcome.tokens_out:
        action_model = str(route.model or "")
        db.add(
            UsageRecord(
                id=uuid.uuid4(),
                tenant_id=user.tenant_id,
                user_id=user.id,
                conversation_id=conversation_id,
                requested_route=getattr(route, "requested_route", None),
                resolved_route=route.resolved_route,
                gateway_provider=getattr(route, "gateway_provider", None),
                gateway_alias=getattr(route, "gateway_alias", None),
                final_model=action_model[:200],
                model_used=action_model[:100],
                tokens_in=outcome.tokens_in,
                tokens_out=outcome.tokens_out,
                # BYOK routes bill the tenant's own provider account, so the
                # platform charges nothing — same rule as the chat completion.
                cost_usd=(
                    Decimal("0")
                    if route.resolved_route == "customer"
                    else calculate_cost(
                        tokens_in=outcome.tokens_in,
                        tokens_out=outcome.tokens_out,
                        model=action_model,
                        billing_tier=(
                            user.tenant.billing_tier if user.tenant else "payg"
                        ),
                    )
                ),
                # Distinct operation_type so action spend can be separated from
                # answer spend when reviewing margin.
                operation_type="chat_action",
                query_text=retained_gateway_query_text(question),
            )
        )


async def _propose_followthrough_actions(
    db,
    user,
    *,
    question: str,
    answer: str,
    rag_context: str,
    route,
    conversation_id,
    use_premium: bool,
    sources: list[dict] | None = None,
) -> tuple[list[dict], str]:
    """Let the assistant propose reviewable follow-through for this turn.

    Deliberately runs *after* the cited answer rather than replacing it, so the
    attorney keeps the sourced analysis whether or not any action is warranted,
    and so chat behaves exactly as before for every tenant without the
    entitlement (the default). Returns the proposal cards plus any text to
    append to the answer.

    Never raises: a proposal is an enhancement, and losing the analysis because
    the action pass failed would be a worse outcome than losing the proposal.
    """
    # The main chat path scrubs provider-bound history and context in privacy
    # mode. The action loop builds a second transcript containing tool results;
    # until that entire transcript has an equivalent, tested scrub boundary,
    # do not send it to another model call or create work from it.
    if getattr(user, "privacy_mode", False):
        logger.info(
            "chat_action_pass_skipped_privacy_mode tenant_id=%s",
            getattr(user, "tenant_id", None),
        )
        return [], ""

    try:
        outcome = await chat_action_agent.run(
            db=db,
            user=user,
            question=question,
            # The answer is the assistant's own reasoning and is what follow-up
            # should be based on; the retrieved context is included so a
            # proposal can cite the same sources.
            rag_context=f"{rag_context}\n\nDRAFT ANALYSIS:\n{answer}".strip(),
            route=route,
            conversation_id=conversation_id,
            use_premium=use_premium,
            # Only sources actually cited in this answer may appear on an
            # action card.  This prevents a model from attaching unrelated
            # tenant material or an invented public-authority id.
            allowed_sources=[
                source
                for source in (sources or [])
                if source.get("cited") and source.get("source_id")
            ],
        )
    except Exception:
        logger.warning("chat_action_pass_failed", exc_info=True)
        return [], ""

    if outcome.halted_reason == "actions_disabled":
        # Never ran, so there is nothing to bill.
        return [], ""
    if outcome.halted_reason:
        # Worth seeing in logs: a budget exhaustion or rejected tool means the
        # assistant wanted to act and could not.
        logger.info(
            "chat_action_pass_halted reason=%s steps=%s",
            outcome.halted_reason,
            outcome.steps_used,
        )

    # Meter the action pass even when it proposed nothing. It is a real second
    # round trip against the same route, and leaving it unrecorded understates
    # cost per conversation and corrupts margin analysis.
    #
    # Guarded because this helper promises never to break the answer: a billing
    # write must not be able to cost the attorney their analysis. A failure here
    # is logged loudly since it means spend went unrecorded.
    try:
        _record_action_usage(
            db,
            user,
            question=question,
            route=route,
            outcome=outcome,
            conversation_id=conversation_id,
        )
    except Exception:
        logger.warning(
            "chat_action_usage_unrecorded tenant_id=%s tokens_in=%s tokens_out=%s",
            user.tenant_id,
            outcome.tokens_in,
            outcome.tokens_out,
            exc_info=True,
        )

    if outcome.needs_input:
        # Asking beats guessing at an owner, a deadline, or a recipient.
        return [], f"\n\n**Before I prepare that:** {outcome.needs_input}"
    if not outcome.proposals:
        return [], ""

    titles = ", ".join(str(p.get("title") or "work") for p in outcome.proposals)
    note = (
        f"\n\n**Proposed for your approval:** {titles}. "
        "It is on the work board in Review — nothing is sent or completed "
        "until you approve it."
    )
    return outcome.proposals, note


async def _trigger_auto_memory_generation_bg(
    user_id: str,
    tenant_id: str,
    conversation_id: str,
    tenant_name: str,
    privacy_mode: bool = False,
) -> None:
    """Background-safe memory generation — creates its own DB session."""
    from app.database import async_session_maker, set_tenant_context

    async with async_session_maker() as db:
        await set_tenant_context(db, tenant_id)
        count_result = await db.execute(
            select(func.count(Message.id)).where(
                Message.conversation_id == conversation_id
            )
        )
        message_count = count_result.scalar() or 0

        if message_count % 10 == 0 and message_count > 0:
            try:
                await memory_service.summarize_conversation(
                    db=db,
                    user_id=user_id,
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                    tenant_name=tenant_name,
                    privacy_mode=privacy_mode,
                )
                await memory_service.update_user_memory_summary(
                    db=db,
                    user_id=user_id,
                    tenant_id=tenant_id,
                    tensor_name=tenant_name,
                )
                await db.commit()
            except Exception:
                logger.warning("Auto-memory generation failed", exc_info=True)


_GENERATION_BUSY_DETAIL = (
    "A response is already being generated for this conversation. "
    "Wait for it to finish before sending or deleting."
)
_MATTER_RELINK_FORBIDDEN_DETAIL = (
    "A conversation with messages or attachments cannot be moved to a different "
    "matter. Start a new conversation for the target matter."
)


async def _await_critical_cleanup(awaitable):
    """Finish DB cleanup even if the surrounding request is cancelled again."""
    task = (
        awaitable
        if isinstance(awaitable, asyncio.Task)
        else asyncio.create_task(awaitable)
    )
    interrupted: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            interrupted = exc
    result = task.result()
    if interrupted is not None:
        raise interrupted
    return result


def _conversation_generation_lock_key(conversation_id) -> int:
    """Return a deterministic namespaced signed-64 Postgres advisory-lock key."""
    conversation_uuid = uuid.UUID(str(conversation_id))
    digest = hashlib.blake2b(
        b"lawhand:chat-generation:v1:" + conversation_uuid.bytes,
        digest_size=8,
    ).digest()
    return int.from_bytes(digest, byteorder="big", signed=True)


@dataclass(slots=True)
class _ConversationGenerationState:
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    conversation_id: uuid.UUID
    user_message_id: uuid.UUID | None = None
    assistant_turn_committed: bool = False


@dataclass(slots=True)
class _ConversationGenerationLease:
    """One pinned connection/session holding a session-level advisory lock."""

    connection: AsyncConnection
    session: AsyncSession
    lock_key: int
    unlock_required: bool = True
    _release_task: asyncio.Task | None = None

    async def _release_impl(self) -> None:
        invalidate = False
        try:
            if self.session.in_transaction():
                await self.session.rollback()
        except BaseException:
            invalidate = True
            logger.exception(
                "Failed to roll back conversation generation session key=%s",
                self.lock_key,
            )

        if not invalidate and self.unlock_required:
            try:
                unlocked = await self.connection.scalar(
                    text("SELECT pg_advisory_unlock(:lock_key)"),
                    {"lock_key": self.lock_key},
                )
                await self.connection.commit()
                if unlocked is not True:
                    logger.error(
                        "Conversation advisory lock was not held at release key=%s",
                        self.lock_key,
                    )
            except BaseException:
                invalidate = True
                logger.exception(
                    "Failed to release conversation advisory lock key=%s",
                    self.lock_key,
                )

        if invalidate:
            # Terminating the physical Postgres session is the fail-safe unlock.
            try:
                await self.connection.invalidate()
            except BaseException:
                logger.exception(
                    "Failed to invalidate conversation lock connection key=%s",
                    self.lock_key,
                )

        try:
            await self.session.close()
        except BaseException:
            logger.exception(
                "Failed to close conversation generation session key=%s",
                self.lock_key,
            )

        try:
            await self.connection.close()
        except BaseException:
            logger.exception(
                "Failed to close conversation lock connection key=%s",
                self.lock_key,
            )
            try:
                await self.connection.invalidate()
            except BaseException:
                logger.exception(
                    "Failed final invalidation of conversation lock connection key=%s",
                    self.lock_key,
                )

    async def release(self) -> None:
        if self._release_task is None:
            self._release_task = asyncio.create_task(self._release_impl())
        await _await_critical_cleanup(self._release_task)


async def _try_conversation_generation_lease(
    db: AsyncSession, conversation_id
) -> _ConversationGenerationLease | None:
    """Hand off a request transaction to one session-locked connection."""
    bind = db.bind
    if bind is None:
        raise RuntimeError("Chat database session is not bound to an engine")
    lock_engine = bind.engine if isinstance(bind, AsyncConnection) else bind

    # The initial ownership lookup used the request session. End that read
    # transaction before pinning the turn connection so an active response
    # consumes exactly one pool slot rather than retaining both.
    await db.rollback()

    connection = await lock_engine.connect()
    lease_session = AsyncSession(
        bind=connection,
        expire_on_commit=False,
        autoflush=False,
    )
    lock_key = _conversation_generation_lock_key(conversation_id)
    lease = _ConversationGenerationLease(
        connection=connection,
        session=lease_session,
        lock_key=lock_key,
    )
    try:
        acquired = await lease_session.scalar(
            text("SELECT pg_try_advisory_lock(:lock_key)"),
            {"lock_key": lock_key},
        )
        # Session-level advisory locks survive this commit, allowing user and
        # assistant rows to become visible while exclusivity remains held.
        await lease_session.commit()
    except BaseException:
        await lease.release()
        raise
    if acquired is not True:
        lease.unlock_required = False
        await lease.release()
        return None
    return lease


async def _owned_conversation_for_generation(
    db: AsyncSession,
    user,
    conversation_id: str,
    *,
    retry_on_miss: bool = False,
) -> Conversation | None:
    attempts = 2 if retry_on_miss else 1
    for attempt in range(attempts):
        conversation = await db.scalar(
            select(Conversation)
            .where(
                Conversation.id == conversation_id,
                Conversation.tenant_id == user.tenant_id,
                Conversation.user_id == user.id,
            )
            .execution_options(populate_existing=True)
        )
        if conversation is not None:
            return conversation
        if retry_on_miss and attempt == 0:
            await asyncio.sleep(0.2)
    return None


async def _persist_interrupted_assistant_turn(
    *,
    db: AsyncSession,
    tenant_id,
    user_id,
    conversation_id,
    user_message_id,
) -> bool:
    """Persist a terminal assistant record for a stream that never committed.

    The user turn is committed before SSE begins. A provider failure, browser
    navigation, or network disconnect must leave a matching assistant state
    rather than a permanently hanging user-only turn. The lease-bound session
    survives request-session handoff and retains the generation lock.
    """
    try:
        await set_tenant_context(db, str(tenant_id))
        conversation = await db.scalar(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.tenant_id == tenant_id,
                Conversation.user_id == user_id,
            )
        )
        if conversation is None:
            return False

        latest = await db.scalar(
            select(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.tenant_id == tenant_id,
            )
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(1)
        )
        if latest is None:
            return False
        if latest.id != user_message_id:
            # The normal assistant commit won the race, or a later turn is
            # already in progress. Never append a misleading stale marker.
            return latest.role == "assistant"

        db.add(
            Message(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                role="assistant",
                content=_STREAM_INTERRUPTED_MESSAGE,
                sources=None,
            )
        )
        conversation.updated_at = datetime.now(timezone.utc)
        await db.commit()
        return True
    except Exception:
        logger.exception(
            "Failed to persist interrupted chat turn tenant_id=%s "
            "conversation_id=%s user_message_id=%s",
            tenant_id,
            conversation_id,
            user_message_id,
        )
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Failed to roll back interrupted-turn persistence "
                "conversation_id=%s",
                conversation_id,
            )
        return False


async def _persist_generation_interruption(
    db: AsyncSession,
    state: _ConversationGenerationState,
) -> bool:
    """Roll back partial work and durably close a committed user turn."""
    try:
        await db.rollback()
    except Exception:
        logger.exception(
            "Failed to roll back interrupted generation conversation_id=%s",
            state.conversation_id,
        )
    return await _persist_interrupted_assistant_turn(
        db=db,
        tenant_id=state.tenant_id,
        user_id=state.user_id,
        conversation_id=state.conversation_id,
        user_message_id=state.user_message_id,
    )


async def _close_interrupted_generation_if_needed(
    db: AsyncSession,
    state: _ConversationGenerationState,
) -> bool:
    if state.user_message_id is None or state.assistant_turn_committed:
        return False
    persisted = await _await_critical_cleanup(
        _persist_generation_interruption(db, state)
    )
    if persisted:
        state.assistant_turn_committed = True
    return persisted


@router.get("", response_model=List[ConversationResponse])
async def list_conversations(
    request: Request,
    db: AsyncSession = Depends(get_db),
    matter_id: str | None = Query(None),
):
    """List all conversations for the current user, optionally filtered by matter."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    conditions = [
        Conversation.user_id == user.id,
        Conversation.tenant_id == user.tenant_id,
    ]
    if matter_id:
        try:
            conditions.append(Conversation.matter_id == uuid.UUID(matter_id))
        except (ValueError, TypeError):
            pass

    result = await db.execute(
        select(Conversation).where(*conditions).order_by(Conversation.updated_at.desc())
    )
    conversations = result.scalars().all()

    # Expose both persisted turn and attachment counts so the client can mirror
    # the backend's immutable matter-context rule after a page reload.
    message_count_map: dict[str, int] = {}
    attachment_count_map: dict[str, int] = {}
    if conversations:
        conversation_ids = [conversation.id for conversation in conversations]
        message_count_result = await db.execute(
            select(Message.conversation_id, func.count(Message.id).label("cnt"))
            .where(
                Message.tenant_id == user.tenant_id,
                Message.conversation_id.in_(conversation_ids),
            )
            .group_by(Message.conversation_id)
        )
        message_count_map = {
            str(row.conversation_id): row.cnt for row in message_count_result.fetchall()
        }
        attachment_count_result = await db.execute(
            select(Document.conversation_id, func.count(Document.id).label("cnt"))
            .where(
                Document.tenant_id == user.tenant_id,
                Document.conversation_id.in_(conversation_ids),
            )
            .group_by(Document.conversation_id)
        )
        attachment_count_map = {
            str(row.conversation_id): row.cnt
            for row in attachment_count_result.fetchall()
        }

    return [
        _conversation_to_response(
            conversation,
            message_count_map.get(str(conversation.id), 0),
            attachment_count_map.get(str(conversation.id), 0),
        )
        for conversation in conversations
    ]


@router.post("", response_model=ConversationResponse, status_code=201)
async def create_conversation(
    body: ConversationCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Create a new conversation."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    matter_uuid = None
    matter_name = None
    if body.matter_id:
        matter = await _matter_for_tenant_or_400(db, user, body.matter_id)
        matter_uuid = matter.id
        matter_name = matter.matter_name

    title = body.title or (
        f"Chat: {matter_name}" if matter_name else "New Conversation"
    )
    conv = Conversation(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        user_id=user.id,
        title=title,
        matter_id=matter_uuid,
    )
    db.add(conv)
    await db.flush()
    await db.commit()

    return _conversation_to_response(conv, 0)


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Get a conversation with all its messages."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    result = await db.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.tenant_id == user.tenant_id,
            Conversation.user_id == user.id,
        )
    )
    conv = result.scalar_one_or_none()

    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if not _conversation_belongs_to_user(conv, user):
        raise HTTPException(status_code=403, detail="Access denied")

    msg_result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conv.id)
        .order_by(Message.created_at.asc(), Message.id.asc())
    )
    messages = msg_result.scalars().all()
    attachment_count = await db.scalar(
        select(func.count(Document.id)).where(
            Document.conversation_id == conv.id,
            Document.tenant_id == user.tenant_id,
        )
    )

    # Batch-load artifacts for all messages so document cards render on reload.
    artifact_map: dict[str, list[ChatArtifact]] = {}
    if messages:
        art_result = await db.execute(
            select(ChatArtifact)
            .where(
                ChatArtifact.message_id.in_([m.id for m in messages]),
                ChatArtifact.tenant_id == user.tenant_id,
            )
            .order_by(ChatArtifact.created_at.asc())
        )
        for artifact in art_result.scalars().all():
            if artifact.message_id is not None:
                artifact_map.setdefault(str(artifact.message_id), []).append(artifact)

    return ConversationDetail(
        conversation=_conversation_to_response(
            conv,
            len(messages),
            attachment_count or 0,
        ),
        messages=[
            _message_to_response(m, artifact_map.get(str(m.id), []))
            for m in messages
        ],
    )


@router.patch("/{conversation_id}", response_model=ConversationResponse)
async def update_conversation(
    conversation_id: str,
    body: ConversationUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Update conversation metadata for the current user."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    result = await db.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.tenant_id == user.tenant_id,
            Conversation.user_id == user.id,
        )
    )
    conv = result.scalar_one_or_none()

    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if not _conversation_belongs_to_user(conv, user):
        raise HTTPException(status_code=403, detail="Access denied")

    tenant_id = user.tenant_id
    title = None
    if body.title is not None:
        title = body.title.strip()
        if not title:
            raise HTTPException(
                status_code=400, detail="Conversation title is required"
            )
        if len(title) > 500:
            raise HTTPException(
                status_code=400,
                detail="Conversation title must be 500 characters or less",
            )

    target_matter_id = conv.matter_id
    if body.matter_id is not None:
        matter_id = body.matter_id.strip()
        if not matter_id:
            target_matter_id = None
        else:
            matter = await _matter_for_tenant_or_400(db, user, matter_id)
            target_matter_id = matter.id

    if body.title is None and body.matter_id is None:
        raise HTTPException(status_code=400, detail="No conversation updates provided")

    semantic_matter_change = (
        body.matter_id is not None and target_matter_id != conv.matter_id
    )
    lease = None
    if semantic_matter_change:
        lease = await _try_conversation_generation_lease(db, conv.id)
        if lease is None:
            raise HTTPException(status_code=409, detail=_GENERATION_BUSY_DETAIL)

    mutation_db = db
    try:
        if lease is not None:
            mutation_db = lease.session
            await set_tenant_context(mutation_db, str(tenant_id))
            locked_user = await get_current_user(request, mutation_db)
            conv = await _owned_conversation_for_generation(
                mutation_db,
                locked_user,
                conversation_id,
            )
            if conv is None:
                raise HTTPException(
                    status_code=404,
                    detail="Conversation not found",
                )
            # Re-resolve the target after acquiring the lease so the applied
            # semantic state is based on a fresh ownership snapshot.
            if body.matter_id is not None:
                matter_id = body.matter_id.strip()
                if matter_id:
                    matter = await _matter_for_tenant_or_400(
                        mutation_db,
                        locked_user,
                        matter_id,
                    )
                    target_matter_id = matter.id
                else:
                    target_matter_id = None

            if target_matter_id != conv.matter_id:
                if await _conversation_has_history(
                    mutation_db,
                    conv.id,
                    tenant_id,
                ):
                    raise HTTPException(
                        status_code=409,
                        detail=_MATTER_RELINK_FORBIDDEN_DETAIL,
                    )

        if title is not None:
            conv.title = title
        if body.matter_id is not None:
            conv.matter_id = target_matter_id

        count_result = await mutation_db.execute(
            select(func.count(Message.id)).where(Message.conversation_id == conv.id)
        )
        message_count = count_result.scalar() or 0
        attachment_count = await mutation_db.scalar(
            select(func.count(Document.id)).where(
                Document.conversation_id == conv.id,
                Document.tenant_id == tenant_id,
            )
        )
        await mutation_db.commit()
        return _conversation_to_response(conv, message_count, attachment_count or 0)
    finally:
        if lease is not None:
            await lease.release()


@router.post(
    "/{conversation_id}/attachments",
    response_model=ChatAttachmentResponse,
    status_code=201,
)
async def upload_chat_attachment(
    conversation_id: str,
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Upload a session attachment for a chat conversation (Tier 1 — no embeddings).

    Misc conversations (no matter_id) get rolling temp storage with a TTL
    (UPLOAD_DIR/{tenant_id}/chat-temp/{conversation_id}/), cleaned up by the
    chat-attachment-cleanup scheduled job. Matter-linked conversations persist
    under the matter's chatattachments subdirectory
    (UPLOAD_DIR/{tenant_id}/matters/{matter_slug}/chatattachments/).
    """
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    result = await db.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.tenant_id == user.tenant_id,
            Conversation.user_id == user.id,
        )
    )
    conv = result.scalar_one_or_none()

    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if not _conversation_belongs_to_user(conv, user):
        raise HTTPException(status_code=403, detail="Access denied")

    tenant_id = user.tenant_id
    conversation_uuid = conv.id
    lease = await _try_conversation_generation_lease(db, conversation_uuid)
    if lease is None:
        raise HTTPException(status_code=409, detail=_GENERATION_BUSY_DETAIL)

    locked_db = lease.session
    storage_path = None
    commit_started = False
    try:
        await set_tenant_context(locked_db, str(tenant_id))
        locked_user = await get_current_user(request, locked_db)
        conv = await _owned_conversation_for_generation(
            locked_db,
            locked_user,
            conversation_id,
        )
        if conv is None:
            raise HTTPException(status_code=404, detail="Conversation not found")

        if not file.filename:
            raise HTTPException(status_code=400, detail="Filename is required")

        max_bytes = settings.MAX_FILE_SIZE_MB * 1024 * 1024
        file_bytes = await file.read()
        if len(file_bytes) > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"File exceeds maximum size of {settings.MAX_FILE_SIZE_MB}MB",
            )

        document_id = uuid.uuid4()
        safe_filename = os.path.basename(file.filename)
        expires_at = None

        if conv.matter_id:
            matter_result = await locked_db.execute(
                select(MatterModel.slug).where(
                    MatterModel.id == conv.matter_id,
                    MatterModel.tenant_id == locked_user.tenant_id,
                )
            )
            matter_row = matter_result.one_or_none()
            matter_slug = matter_row[0] if matter_row else str(conv.matter_id)
            storage_dir = os.path.join(
                settings.UPLOAD_DIR,
                str(locked_user.tenant_id),
                "matters",
                matter_slug,
                "chatattachments",
                str(document_id),
            )
        else:
            storage_dir = os.path.join(
                settings.UPLOAD_DIR,
                str(locked_user.tenant_id),
                "chat-temp",
                str(conversation_id),
                str(document_id),
            )
            expires_at = datetime.now(timezone.utc) + timedelta(
                days=settings.CHAT_ATTACHMENT_TTL_DAYS
            )

        os.makedirs(storage_dir, exist_ok=True)
        storage_path = os.path.join(storage_dir, safe_filename)
        async with aiofiles.open(storage_path, "wb") as out_file:
            await out_file.write(file_bytes)

        doc = Document(
            id=document_id,
            tenant_id=locked_user.tenant_id,
            user_id=locked_user.id,
            conversation_id=conv.id,
            matter_id=conv.matter_id,
            filename=safe_filename,
            content_type=file.content_type,
            file_size=len(file_bytes),
            storage_path=storage_path,
            status="ready",
            chunk_count=0,
            expires_at=expires_at,
        )
        locked_db.add(doc)
        await locked_db.flush()
        commit_started = True
        await locked_db.commit()
        return ChatAttachmentResponse.model_validate(doc)
    except BaseException:
        if not commit_started and storage_path and os.path.exists(storage_path):
            try:
                shutil.rmtree(os.path.dirname(storage_path))
            except OSError:
                logger.warning(
                    "Failed to clean up chat attachment after upload failure",
                    exc_info=True,
                )
        raise
    finally:
        await lease.release()


@router.delete("/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Delete a conversation (must belong to user)."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    result = await db.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.tenant_id == user.tenant_id,
            Conversation.user_id == user.id,
        )
    )
    conv = result.scalar_one_or_none()

    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if not _conversation_belongs_to_user(conv, user):
        raise HTTPException(status_code=403, detail="Access denied")

    tenant_id = user.tenant_id
    conversation_uuid = conv.id
    lease = await _try_conversation_generation_lease(db, conversation_uuid)
    if lease is None:
        raise HTTPException(status_code=409, detail=_GENERATION_BUSY_DETAIL)

    locked_db = lease.session
    try:
        await set_tenant_context(locked_db, str(tenant_id))
        locked_user = await get_current_user(request, locked_db)
        # Recheck after acquisition so a delete that won the lock race cannot
        # leave this request operating on stale ownership state.
        conv = await _owned_conversation_for_generation(
            locked_db,
            locked_user,
            conversation_id,
        )
        if conv is None:
            raise HTTPException(status_code=404, detail="Conversation not found")

        # Snapshot paths while their rows still exist, but commit the cascade
        # first. A failed/ambiguous commit must never leave live rows pointing
        # at files this request already erased.
        attachment_result = await locked_db.execute(
            select(Document.storage_path).where(Document.conversation_id == conv.id)
        )
        attachment_dirs = {
            os.path.dirname(storage_path)
            for (storage_path,) in attachment_result.all()
            if storage_path
        }

        await locked_db.delete(conv)
        await locked_db.commit()
        for attachment_dir in attachment_dirs:
            shutil.rmtree(attachment_dir, ignore_errors=True)
    finally:
        await lease.release()


@router.post(
    "/{conversation_id}/messages", response_model=MessageResponse, status_code=201
)
async def send_message(
    conversation_id: str,
    body: MessageCreate,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Serialize one complete non-stream generation per conversation."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    conv = await _owned_conversation_for_generation(db, user, conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    state = _ConversationGenerationState(
        tenant_id=user.tenant_id,
        user_id=user.id,
        conversation_id=conv.id,
    )
    lease = await _try_conversation_generation_lease(db, state.conversation_id)
    if lease is None:
        raise HTTPException(status_code=409, detail=_GENERATION_BUSY_DETAIL)

    locked_db = lease.session
    try:
        await set_tenant_context(locked_db, str(state.tenant_id))
        locked_user = await get_current_user(request, locked_db)
        conv = await _owned_conversation_for_generation(
            locked_db,
            locked_user,
            conversation_id,
        )
        if conv is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        return await _send_message_under_generation_lock(
            conversation_id,
            body,
            request,
            background_tasks,
            locked_db,
            generation_state=state,
        )
    except asyncio.CancelledError:
        await _close_interrupted_generation_if_needed(locked_db, state)
        raise
    except HTTPException:
        await _close_interrupted_generation_if_needed(locked_db, state)
        raise
    except Exception as exc:
        await _close_interrupted_generation_if_needed(locked_db, state)
        logger.exception(
            "Non-stream chat failed tenant_id=%s user_id=%s conversation_id=%s",
            state.tenant_id,
            state.user_id,
            state.conversation_id,
        )
        raise HTTPException(
            status_code=502,
            detail="Assistant service temporarily unavailable. Retry this message.",
        ) from exc
    finally:
        await lease.release()


async def _send_message_under_generation_lock(
    conversation_id: str,
    body: MessageCreate,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession,
    *,
    generation_state: _ConversationGenerationState,
):
    """
    Main RAG endpoint — processes a user message and returns an AI response.
    1. Validate conversation ownership
    2. Save user message
    3. Run RAG pipeline
    4. Generate LLM response
    5. Apply guardrails
    6. Save assistant message with sources
    7. Record usage
    8. Return MessageResponse
    """
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    # 1. Validate conversation belongs to user's tenant
    result = await db.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.tenant_id == user.tenant_id,
            Conversation.user_id == user.id,
        )
    )
    conv = result.scalar_one_or_none()

    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if not _conversation_belongs_to_user(conv, user):
        raise HTTPException(status_code=403, detail="Access denied")

    reject_demo_premium(user, body.use_premium_llm)
    use_premium = _premium_for_user(user, body.content, body.use_premium_llm)
    route = await resolve_llm_route(
        db,
        user.tenant_id,
        use_premium=use_premium,
        requested_provider=body.provider,
    )
    public_general = _is_public_general_route(route)
    privacy_mode = public_general or bool(getattr(user, "privacy_mode", False))
    if public_general:
        _assert_public_general_sources_allowed(conv, body)

    effective_matter = (
        None
        if public_general
        else await _effective_message_matter(db, user, conv, body)
    )
    effective_matter_id = str(effective_matter.id) if effective_matter else None
    matter_context_enabled = bool(
        effective_matter
    ) and await matter_context_service.is_enabled(db, user.tenant_id)
    context_matter_id = effective_matter_id if matter_context_enabled else None
    context_matter_cloud_folder = (
        effective_matter.cloud_folder if matter_context_enabled else None
    )
    default_public_jurisdiction = select_public_jurisdiction_default(
        effective_matter.jurisdiction if matter_context_enabled else None,
        getattr(user, "primary_jurisdictions", None),
    )
    rag_scope_key = _rag_scope_key(
        context_matter_id,
        context_matter_cloud_folder,
        default_public_jurisdiction,
    )

    # 1a. Enforce daily token budget (fail fast before any work is done)
    await check_token_budget(db, user)

    # 2. Check for PII in user input
    pii_findings = check_pii_in_input(body.content) if privacy_mode else []

    # 2a. Save user message with PII flags
    user_msg = Message(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        conversation_id=conv.id,
        role="user",
        content=body.content,
        sources=None,
        pii_flags=pii_findings if pii_findings else None,
    )
    db.add(user_msg)
    await db.flush()
    generation_state.user_message_id = user_msg.id

    # 3. Load recent conversation history (last 10 messages)
    history_result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conv.id)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(11)  # 10 + the one we just added
    )
    recent_messages = list(reversed(history_result.scalars().all()))
    # Exclude the message we just added (it's the last one)
    history_messages = (
        []
        if public_general
        else [
            {"role": m.role, "content": m.content}
            for m in recent_messages
            if str(m.id) != str(user_msg.id)
        ][-10:]
    )
    llm_messages = prepare_provider_messages(
        history_messages + [{"role": "user", "content": body.content}],
        privacy_mode,
    )
    provider_question = prepare_provider_text(body.content, privacy_mode)

    # The submitted turn is durable before retrieval/provider work begins. If a
    # downstream dependency fails, the user's message remains visible and can
    # be retried instead of disappearing with the request transaction.
    conv.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await set_tenant_context(db, str(user.tenant_id))

    # 3a. Load DB-backed pre-work serially on the lease-bound session. AsyncSession
    #     cannot safely run concurrent statements, and opening helper sessions
    #     would defeat the one-connection-per-active-turn availability invariant.
    async def _load_matter_context_nonstream():
        if not context_matter_id:
            return "", [], None, False

        cached = await cache_manager.get_cached_matter_context(
            matter_id=context_matter_id,
            tenant_id=str(user.tenant_id),
            privacy_mode=privacy_mode,
        )
        if cached:
            return cached, [], context_matter_cloud_folder, True
        mcs, has_pii, mpf = await matter_context_service.get_safe_matter_context(
            db=db,
            matter_id=context_matter_id,
            tenant_id=user.tenant_id,
            privacy_mode=privacy_mode,
        )
        await cache_manager.set_cached_matter_context(
            matter_id=context_matter_id,
            tenant_id=str(user.tenant_id),
            context=mcs,
            expertise_level=user.expertise_level,
            privacy_mode=privacy_mode,
        )
        return mcs, mpf if mpf else [], context_matter_cloud_folder, False

    async def _load_attachment_context_nonstream():
        return await _build_attachment_context(
            db,
            user,
            conv,
            body.attachment_ids if hasattr(body, "attachment_ids") else None,
        )

    async def _load_memory_context_nonstream():
        return await memory_service.get_memory_context_for_injection(
            db=db, user_id=user.id
        )

    if public_general:
        (
            matter_context_str,
            matter_pii_findings,
            matter_cloud_folder,
            cache_hit_matter,
        ) = (
            "",
            [],
            None,
            False,
        )
        attachment_context, attachment_sources = "", []
        memory_context = ""
    else:
        (
            matter_context_str,
            matter_pii_findings,
            matter_cloud_folder,
            cache_hit_matter,
        ) = await _load_matter_context_nonstream()
        (
            attachment_context,
            attachment_sources,
        ) = await _load_attachment_context_nonstream()
        memory_context = await _load_memory_context_nonstream()
    rag_cache_revision = str(
        await db.scalar(
            select(Tenant.rag_corpus_revision).where(Tenant.id == user.tenant_id)
        )
        or 0
    )
    cached_rag = (
        None
        if public_general
        else await cache_manager.get_cached_rag_results(
            question=body.content,
            tenant_id=str(user.tenant_id),
            user_id=str(user.id),
            skill=body.skill if hasattr(body, "skill") else user.default_skill,
            include_public=body.include_public,
            scope_key=rag_scope_key,
            corpus_revision=rag_cache_revision,
        )
    )

    # 4. RAG: embed question, search chunks, build context (with caching)
    cache_hit_rag = False
    cloud_hits = []
    if cached_rag:
        context_str, chunks, cloud_hits = cached_rag
        cache_hit_rag = True
    else:
        try:
            context_str, chunks, cloud_hits = await hybrid_rag_query(
                db=db,
                embedding_service=embedding_service,
                question=provider_question,
                tenant_id=str(user.tenant_id),
                user_id=str(user.id),
                include_public=body.include_public,
                include_private=not public_general,
                cloud_search_service=_get_cloud_search_service(),
                retrieval_planner=_get_retrieval_planner(),
                tenant_name=prepare_provider_text(
                    user.tenant.name if user.tenant else "Legal", privacy_mode
                ),
                matter_context_str=prepare_provider_text(
                    matter_context_str, privacy_mode
                ),
                matter_id=context_matter_id,
                matter_cloud_folder=matter_cloud_folder,
                default_public_jurisdiction=default_public_jurisdiction,
            )
            await set_tenant_context(db, str(user.tenant_id))
            if not public_general and rag_result_is_cacheable(
                context_str, chunks, cloud_hits
            ):
                await cache_manager.set_cached_rag_results(
                    question=body.content,
                    tenant_id=str(user.tenant_id),
                    user_id=str(user.id),
                    context_str=context_str,
                    chunks=chunks,
                    cloud_hits=cloud_hits,
                    expertise_level=user.expertise_level,
                    skill=body.skill if hasattr(body, "skill") else user.default_skill,
                    include_public=body.include_public,
                    scope_key=rag_scope_key,
                    expected_corpus_revision=rag_cache_revision,
                )
            else:
                logger.info("Skipping RAG cache write for empty or degraded retrieval")
        except Exception as rag_exc:
            logger.exception("RAG query failed")
            await capture_chat_error(
                db=db,
                error_type="rag_query_error",
                message=f"RAG query failed: {rag_exc}",
                user_id=user.id,
                tenant_id=user.tenant_id,
                request=request,
                query_text=provider_question,
                conversation_id=conv.id,
                severity="error",
            )
            await set_tenant_context(db, str(user.tenant_id))
            context_str, chunks = "", []

    # 4a. Combine attachment, matter, and RAG context
    context_str = _join_context_sections(
        attachment_context,
        matter_context_str,
        context_str,
    )
    context_str = prepare_provider_text(context_str, privacy_mode)
    memory_context = prepare_provider_text(memory_context, privacy_mode)
    global_user_context = "" if public_general else build_global_user_context(user)

    # 5. Call LLM. Conversational answers are deliberately not response-cached:
    # history and injected memory are part of the semantic input, and serving a
    # stale answer is a worse failure mode than paying for a fresh completion.
    tenant_name = (
        "Legal"
        if public_general
        else prepare_provider_text(
            user.tenant.name if user.tenant else "Legal", privacy_mode
        )
    )
    user_first_name = (
        ""
        if public_general
        else prepare_provider_text(
            (user.full_name or "").split()[0] if user.full_name else "",
            privacy_mode,
        )
    )

    cache_hit_llm = False
    tokens_in, tokens_out = 0, 0
    llm_usage: dict = {}
    try:
        response_text, tokens_in, tokens_out = await llm_service.complete(
            messages=llm_messages,
            tenant_name=tenant_name,
            context=context_str,
            memory_context=memory_context,
            global_user_context=global_user_context,
            use_premium=use_premium,
            provider=route.provider,
            model=route.model,
            user_name=user_first_name,
            customer_api_key=route.customer_api_key,
            customer_provider=route.customer_provider,
            customer_endpoint=route.customer_endpoint,
            gateway_metadata=gateway_metadata(
                tenant_id=user.tenant_id,
                user_id=user.id,
                conversation_id=conv.id,
                operation_type="chat",
                matter_id=None if public_general else effective_matter_id,
                skill=None
                if public_general
                else (body.skill if hasattr(body, "skill") else user.default_skill),
                premium=use_premium,
            ),
            system_prompt_override=(
                llm_service.public_general_system_prompt() if public_general else None
            ),
            usage_sink=llm_usage,
        )
    except Exception as llm_exc:
        logger.exception("LLM call failed")
        await capture_chat_error(
            db=db,
            error_type="llm_error",
            message=f"LLM call failed: {llm_exc}",
            user_id=user.id,
            tenant_id=user.tenant_id,
            request=request,
            query_text=provider_question,
            conversation_id=conv.id,
            severity="error",
        )
        await db.commit()
        raise HTTPException(
            status_code=502, detail="LLM service temporarily unavailable"
        )

    # 6. Apply guardrails (check for AI self-disclosure and PII in privacy mode)
    cleaned_response, needs_retry, response_pii = apply_guardrails(
        response_text, privacy_mode=privacy_mode
    )

    if needs_retry:
        # Retry once with an explicit instruction
        retry_messages = llm_messages + [
            {
                "role": "assistant",
                "content": "I need to revise my response to focus on legal analysis.",
            },
        ]
        response_text2, tokens_in2, tokens_out2 = await llm_service.complete(
            messages=retry_messages,
            tenant_name=tenant_name,
            context=context_str,
            memory_context=memory_context,
            global_user_context=global_user_context,
            use_premium=use_premium,
            provider=route.provider,
            model=route.model,
            user_name=user_first_name,
            customer_api_key=route.customer_api_key,
            customer_provider=route.customer_provider,
            customer_endpoint=route.customer_endpoint,
            gateway_metadata=gateway_metadata(
                tenant_id=user.tenant_id,
                user_id=user.id,
                conversation_id=conv.id,
                operation_type="chat_retry",
                matter_id=None if public_general else effective_matter_id,
                skill=None
                if public_general
                else (body.skill if hasattr(body, "skill") else user.default_skill),
                premium=use_premium,
            ),
            system_prompt_override=(
                llm_service.public_general_system_prompt() if public_general else None
            ),
            usage_sink=llm_usage,
        )
        cleaned_response, _, response_pii = apply_guardrails(
            response_text2, privacy_mode=privacy_mode
        )
        tokens_in += tokens_in2
        tokens_out += tokens_out2

    # Build source citations from retrieved chunks with relevance scores
    source_dicts = list(attachment_sources)
    context_used = [
        source["source_id"] for source in attachment_sources if source.get("source_id")
    ]
    context_scores = {source_id: 1.0 for source_id in context_used}
    seen_citations: dict[str, str] = {}
    source_aliases: dict[str, str] = {}
    for source in attachment_sources:
        source_id = source.get("source_id")
        for citation_key in (source.get("citation"), source.get("case_name")):
            if source_id and citation_key:
                seen_citations[str(citation_key)] = str(source_id)
    for chunk in chunks:
        chunk_id = str(chunk.get("id", f"chunk_{len(context_used)}"))
        citation_key = chunk.get("citation") or chunk.get("case_name") or ""
        if citation_key and citation_key in seen_citations:
            source_aliases[chunk_id] = seen_citations[citation_key]
            continue
        if citation_key:
            seen_citations[citation_key] = chunk_id
        context_used.append(chunk_id)
        context_scores[chunk_id] = chunk.get("relevance_score", 0.5)
        source_dicts.append(_source_dict_from_chunk(chunk))

    for hit in cloud_hits:
        hit_dict = _cloud_hit_dict(hit)
        cloud_id = _cloud_hit_context_id(hit_dict)
        if cloud_id in seen_citations:
            continue
        seen_citations[cloud_id] = cloud_id
        context_used.append(cloud_id)
        context_scores[cloud_id] = hit_dict.get("relevance_score", 0.5)
        source_dicts.append(_source_dict_from_cloud_hit(hit_dict))

    cleaned_response = _canonicalize_source_references(cleaned_response, source_aliases)
    cleaned_response, _ = reconcile_retrieved_source_attribution(
        cleaned_response, source_dicts
    )
    cleaned_response, _ = consolidate_unverified_model_knowledge(
        cleaned_response, source_dicts
    )
    cleaned_response, _ = validate_citation_confidence(
        cleaned_response,
        _citation_validation_sources(source_dicts, chunks, cloud_hits),
    )
    cleaned_response, citation_gap = enforce_legal_citation_integrity(
        body.content,
        cleaned_response,
        source_dicts,
    )
    if citation_gap:
        logger.warning(
            "Blocked unsupported legal-research answer conversation_id=%s",
            conv.id,
        )
    source_dicts = _mark_cited_sources(cleaned_response, source_dicts)

    # Track matter context usage if provided
    if context_matter_id:
        context_used.insert(0, f"matter:{context_matter_id}")
        context_scores[f"matter:{context_matter_id}"] = 1.0

    # Combine PII findings from input, response, and matter context
    all_pii_flags = []
    if pii_findings:
        all_pii_flags.extend([{"source": "input", **pf} for pf in pii_findings])
    if response_pii:
        all_pii_flags.extend([{"source": "response", **pf} for pf in response_pii])
    if matter_pii_findings:
        all_pii_flags.extend(
            [{"source": "matter_context", **pf} for pf in matter_pii_findings]
        )

    # 7. Save assistant message with context tracking and skill info
    model_used = str(llm_usage.get("model") or route.model)
    skill_applied = (
        body.skill if hasattr(body, "skill") and body.skill else user.default_skill
    )
    await set_tenant_context(db, str(user.tenant_id))

    proposed_actions, action_note = (
        ([], "")
        if public_general
        else await _propose_followthrough_actions(
            db,
            user,
            question=body.content,
            answer=cleaned_response,
            rag_context=context_str,
            route=route,
            conversation_id=conv.id,
            use_premium=use_premium,
            sources=source_dicts,
        )
    )
    if action_note:
        cleaned_response = f"{cleaned_response}{action_note}"

    assistant_msg = Message(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        conversation_id=conv.id,
        role="assistant",
        content=cleaned_response,
        sources=source_dicts if source_dicts else None,
        skill_applied=skill_applied,
        context_used=context_used if context_used else None,
        context_relevance_scores=context_scores if context_scores else None,
        pii_flags=all_pii_flags if all_pii_flags else None,
        proposed_actions=proposed_actions or None,
    )
    db.add(assistant_msg)

    # Extract document artifacts (e.g. drafted clauses, memos) from the
    # response. Artifact blocks are stripped from the visible message body and
    # persisted as ChatArtifact rows linked to this message.
    visible_content, msg_artifacts = await _persist_message_artifacts(
        db,
        user=user,
        conv=conv,
        assistant_msg=assistant_msg,
        response_text=cleaned_response,
    )
    assistant_msg.content = visible_content

    # Update conversation updated_at
    conv.updated_at = datetime.now(timezone.utc)

    # 8. Record usage
    # BYOK (customer) routes use the tenant's own provider subscription —
    # the platform does not pay for those tokens, so don't bill for them.
    cost = (
        Decimal("0")
        if route.resolved_route == "customer"
        else calculate_cost(
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            model=model_used,
            billing_tier=user.tenant.billing_tier if user.tenant else "payg",
        )
    )
    cloud_source_ids = [
        _cloud_hit_context_id(_cloud_hit_dict(hit)) for hit in cloud_hits
    ]
    usage = UsageRecord(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        user_id=user.id,
        conversation_id=conv.id,
        requested_route=route.requested_route,
        resolved_route=route.resolved_route,
        gateway_provider=route.gateway_provider,
        gateway_alias=route.gateway_alias,
        final_model=model_used[:200],
        model_used=model_used[:100],
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost,
        operation_type="chat",
        query_text=retained_gateway_query_text(provider_question),
        rag_chunks_retrieved=len(chunks),
        rag_source_ids=[c["id"] for c in chunks if c.get("id")] + cloud_source_ids,
        ip_address=request.client.host if request.client else None,
        user_agent=(request.headers.get("user-agent") or "")[:500] or None,
        cache_hit_rag=cache_hit_rag,
        cache_hit_llm=cache_hit_llm,
        cache_hit_matter=cache_hit_matter,
    )
    db.add(usage)

    await db.flush()
    await db.commit()
    generation_state.assistant_turn_committed = True

    # Trigger auto-memory generation in background (non-blocking)
    if not public_general:
        background_tasks.add_task(
            _trigger_auto_memory_generation_bg,
            user_id=str(user.id),
            tenant_id=str(user.tenant_id),
            conversation_id=str(conv.id),
            tenant_name=user.tenant.name if user.tenant else "Legal",
            privacy_mode=privacy_mode,
        )

    return _message_to_response(assistant_msg, msg_artifacts)


@router.post("/{conversation_id}/messages/stream")
async def stream_message(
    conversation_id: str,
    body: MessageCreate,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Serialize a streamed response until its terminal DB state is durable."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    conv = await _owned_conversation_for_generation(
        db,
        user,
        conversation_id,
        retry_on_miss=True,
    )
    if conv is None:
        return StreamingResponse(
            _error_stream(
                "Conversation not found — please try sending your message again."
            ),
            media_type="text/event-stream",
        )

    state = _ConversationGenerationState(
        tenant_id=user.tenant_id,
        user_id=user.id,
        conversation_id=conv.id,
    )
    lease = await _try_conversation_generation_lease(db, state.conversation_id)
    if lease is None:
        raise HTTPException(status_code=409, detail=_GENERATION_BUSY_DETAIL)

    locked_db = lease.session
    response_owns_lease = False
    try:
        await set_tenant_context(locked_db, str(state.tenant_id))
        locked_user = await get_current_user(request, locked_db)
        conv = await _owned_conversation_for_generation(
            locked_db,
            locked_user,
            conversation_id,
        )
        if conv is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        response = await _stream_message_under_generation_lock(
            conversation_id,
            body,
            request,
            background_tasks,
            locked_db,
            generation_state=state,
        )

        original_iterator = response.body_iterator

        async def locked_body_iterator():
            try:
                async for chunk in original_iterator:
                    yield chunk
            finally:
                try:
                    await _close_interrupted_generation_if_needed(locked_db, state)
                finally:
                    await lease.release()

        response.body_iterator = locked_body_iterator()
        response_owns_lease = True
        return response
    except asyncio.CancelledError:
        await _close_interrupted_generation_if_needed(locked_db, state)
        raise
    except HTTPException:
        await _close_interrupted_generation_if_needed(locked_db, state)
        raise
    except Exception as exc:
        await _close_interrupted_generation_if_needed(locked_db, state)
        logger.exception(
            "Streaming chat setup failed tenant_id=%s user_id=%s conversation_id=%s",
            state.tenant_id,
            state.user_id,
            state.conversation_id,
        )
        raise HTTPException(
            status_code=502,
            detail="Assistant service temporarily unavailable. Retry this message.",
        ) from exc
    finally:
        if not response_owns_lease:
            await lease.release()


async def _stream_message_under_generation_lock(
    conversation_id: str,
    body: MessageCreate,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession,
    *,
    generation_state: _ConversationGenerationState,
):
    """
    Streaming RAG endpoint — same as send_message but returns SSE progress while
    generation is validated, followed by safe answer chunks prefixed with 'data: '.
    Sends [STREAM_COMPLETE] when done, or [ERROR] message on failure.
    """
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    # 1. Validate conversation belongs to user's tenant
    # Retry once on miss — the conversation may have been created moments ago
    # and the prior transaction's commit may not be visible yet on busy DBs.
    conv = None
    for attempt in range(2):
        result = await db.execute(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.tenant_id == user.tenant_id,
                Conversation.user_id == user.id,
            )
        )
        conv = result.scalar_one_or_none()
        if conv is not None:
            break
        if attempt == 0:
            await asyncio.sleep(0.2)

    if conv is None:
        return StreamingResponse(
            _error_stream(
                "Conversation not found — please try sending your message again."
            ),
            media_type="text/event-stream",
        )

    if not _conversation_belongs_to_user(conv, user):
        return StreamingResponse(
            _error_stream("Access denied"),
            media_type="text/event-stream",
        )

    try:
        reject_demo_premium(user, body.use_premium_llm)
        use_premium = _premium_for_user(user, body.content, body.use_premium_llm)
        route = await resolve_llm_route(
            db,
            user.tenant_id,
            use_premium=use_premium,
            requested_provider=body.provider,
        )
        public_general = _is_public_general_route(route)
        privacy_mode = public_general or bool(getattr(user, "privacy_mode", False))
        if public_general:
            _assert_public_general_sources_allowed(conv, body)
        effective_matter = (
            None
            if public_general
            else await _effective_message_matter(db, user, conv, body)
        )
    except HTTPException as matter_exc:
        return StreamingResponse(
            _error_stream(matter_exc.detail),
            media_type="text/event-stream",
        )
    effective_matter_id = str(effective_matter.id) if effective_matter else None
    matter_context_enabled = bool(
        effective_matter
    ) and await matter_context_service.is_enabled(db, user.tenant_id)
    context_matter_id = effective_matter_id if matter_context_enabled else None
    context_matter_cloud_folder = (
        effective_matter.cloud_folder if matter_context_enabled else None
    )
    default_public_jurisdiction = select_public_jurisdiction_default(
        effective_matter.jurisdiction if matter_context_enabled else None,
        getattr(user, "primary_jurisdictions", None),
    )
    rag_scope_key = _rag_scope_key(
        context_matter_id,
        context_matter_cloud_folder,
        default_public_jurisdiction,
    )

    # 1a. Enforce daily token budget
    try:
        await check_token_budget(db, user)
    except HTTPException as budget_exc:
        return StreamingResponse(
            _error_stream(budget_exc.detail),
            media_type="text/event-stream",
        )

    # 2. Check for PII in user input
    pii_findings = check_pii_in_input(body.content) if privacy_mode else []

    # 2a. Save user message with PII flags
    user_msg = Message(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        conversation_id=conv.id,
        role="user",
        content=body.content,
        sources=None,
        pii_flags=pii_findings if pii_findings else None,
    )
    db.add(user_msg)
    await db.flush()
    generation_state.user_message_id = user_msg.id

    # 3. Load recent conversation history (last 10 messages)
    history_result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conv.id)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(11)
    )
    recent_messages = list(reversed(history_result.scalars().all()))
    history_messages = (
        []
        if public_general
        else [
            {"role": m.role, "content": m.content}
            for m in recent_messages
            if str(m.id) != str(user_msg.id)
        ][-10:]
    )
    llm_messages = prepare_provider_messages(
        history_messages + [{"role": "user", "content": body.content}],
        privacy_mode,
    )
    provider_question = prepare_provider_text(body.content, privacy_mode)

    # Commit before returning the StreamingResponse. The generator can fail or
    # the client can disconnect after this point without losing the user turn.
    conv.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await set_tenant_context(db, str(user.tenant_id))

    # The remaining work intentionally happens inside the response generator.
    # This opens SSE immediately so the client can render real retrieval activity
    # instead of staring at an inert composer while all context work finishes.
    async def _load_matter_context():
        if not context_matter_id:
            return "", [], None, False

        cached = await cache_manager.get_cached_matter_context(
            matter_id=context_matter_id,
            tenant_id=str(user.tenant_id),
            privacy_mode=privacy_mode,
        )
        if cached:
            return cached, [], context_matter_cloud_folder, True
        mcs, has_pii, mpf = await matter_context_service.get_safe_matter_context(
            db=db,
            matter_id=context_matter_id,
            tenant_id=user.tenant_id,
            privacy_mode=privacy_mode,
        )
        await cache_manager.set_cached_matter_context(
            matter_id=context_matter_id,
            tenant_id=str(user.tenant_id),
            context=mcs,
            expertise_level=user.expertise_level,
            privacy_mode=privacy_mode,
        )
        return mcs, mpf if mpf else [], context_matter_cloud_folder, False

    async def _load_attachment_context_for_stream():
        return await _build_attachment_context(
            db,
            user,
            conv,
            body.attachment_ids if hasattr(body, "attachment_ids") else None,
        )

    async def _load_memory_context_for_stream():
        return await memory_service.get_memory_context_for_injection(
            db=db, user_id=user.id
        )

    async def _load_stream_prework():
        if public_general:
            matter_context = ("", [], None, False)
            attachment_context = ("", [])
            memory_context = ""
        else:
            matter_context = await _load_matter_context()
            attachment_context = await _load_attachment_context_for_stream()
            memory_context = await _load_memory_context_for_stream()
        rag_cache_revision = str(
            await db.scalar(
                select(Tenant.rag_corpus_revision).where(Tenant.id == user.tenant_id)
            )
            or 0
        )
        cached_rag = (
            None
            if public_general
            else await cache_manager.get_cached_rag_results(
                question=body.content,
                tenant_id=str(user.tenant_id),
                user_id=str(user.id),
                skill=body.skill if hasattr(body, "skill") else user.default_skill,
                include_public=body.include_public,
                scope_key=rag_scope_key,
                corpus_revision=rag_cache_revision,
            )
        )
        return (
            matter_context,
            attachment_context,
            memory_context,
            route,
            cached_rag,
            rag_cache_revision,
        )

    attachment_count = len(body.attachment_ids or [])

    # Create the streaming generator
    stream_user_first_name = (
        ""
        if public_general
        else prepare_provider_text(
            (user.full_name or "").split()[0] if user.full_name else "",
            privacy_mode,
        )
    )
    stream_global_user_context = (
        "" if public_general else build_global_user_context(user)
    )
    # Keep primitive identifiers for the exception path. A rollback expires ORM
    # instances; reading user/tenant attributes afterward can trigger async IO
    # outside SQLAlchemy's greenlet and hide the original stream failure.
    stream_tenant_id = user.tenant_id
    stream_user_id = user.id
    stream_conversation_id = conv.id
    stream_user_message_id = user_msg.id

    async def stream_generator():
        assistant_turn_committed = False
        terminal_failure_persisted = False
        try:
            stream_started_at = time.monotonic()
            latency_breakdown: dict[str, int] = {}

            yield _stream_activity_event(
                "understanding",
                "started",
                "Understanding your request",
                detail="Identifying the legal task and source scope",
            )
            yield _stream_activity_event(
                "understanding",
                "completed",
                "Request understood",
                elapsed_ms=0,
            )

            prework_started_at = time.monotonic()
            yield _stream_activity_event(
                "working_context",
                "started",
                "Loading matter, attachments, and saved context",
            )
            (
                (
                    matter_context_str,
                    matter_pii_findings,
                    matter_cloud_folder,
                    cache_hit_matter,
                ),
                (attachment_context, attachment_sources),
                memory_context,
                route,
                cached_rag,
                rag_cache_revision,
            ) = await _load_stream_prework()
            latency_breakdown["context_ms"] = int(
                (time.monotonic() - prework_started_at) * 1000
            )
            context_counts = _stream_source_counts(
                chunks=[],
                cloud_hits=[],
                has_matter_context=bool(
                    matter_context_str and matter_context_str.strip()
                ),
                attachment_count=attachment_count,
            )
            yield _stream_activity_event(
                "working_context",
                "completed",
                "Working context ready",
                elapsed_ms=latency_breakdown["context_ms"],
                counts=context_counts,
                detail=(
                    "Used cached matter context"
                    if cache_hit_matter
                    else "Loaded current matter context"
                ),
            )

            retrieval_started_at = time.monotonic()
            yield _stream_activity_event(
                "firm_search",
                "started",
                "Searching firm knowledge",
                detail="Hybrid vector and keyword retrieval",
            )
            if body.include_public:
                yield _stream_activity_event(
                    "public_authority",
                    "started",
                    "Checking cases, statutes, and rules",
                    detail="CourtListener and public authority search",
                )

            cache_hit_rag = False
            cloud_hits = []
            rag_cache_task = None
            if cached_rag:
                context_str, chunks, cloud_hits = cached_rag
                cache_hit_rag = True
            else:
                try:
                    context_str, chunks, cloud_hits = await hybrid_rag_query(
                        db=db,
                        embedding_service=embedding_service,
                        question=provider_question,
                        tenant_id=str(user.tenant_id),
                        user_id=str(user.id),
                        include_public=body.include_public,
                        include_private=not public_general,
                        cloud_search_service=_get_cloud_search_service(),
                        retrieval_planner=_get_retrieval_planner(),
                        tenant_name=prepare_provider_text(
                            user.tenant.name if user.tenant else "Legal",
                            privacy_mode,
                        ),
                        matter_context_str=prepare_provider_text(
                            matter_context_str, privacy_mode
                        ),
                        matter_id=context_matter_id,
                        matter_cloud_folder=matter_cloud_folder,
                        default_public_jurisdiction=default_public_jurisdiction,
                    )
                except Exception:
                    logger.exception(
                        "RAG query failed in streaming path, continuing without context"
                    )
                    context_str, chunks = "", []
                await set_tenant_context(db, str(user.tenant_id))
                if not public_general and rag_result_is_cacheable(
                    context_str, chunks, cloud_hits
                ):
                    # Cache writes should never hold up model time-to-first-token.
                    # Rejoin before stream completion so failures are observed.
                    rag_cache_task = asyncio.create_task(
                        cache_manager.set_cached_rag_results(
                            question=body.content,
                            tenant_id=str(user.tenant_id),
                            user_id=str(user.id),
                            context_str=context_str,
                            chunks=chunks,
                            cloud_hits=cloud_hits,
                            expertise_level=user.expertise_level,
                            skill=body.skill
                            if hasattr(body, "skill")
                            else user.default_skill,
                            include_public=body.include_public,
                            scope_key=rag_scope_key,
                            expected_corpus_revision=rag_cache_revision,
                        )
                    )
                else:
                    logger.info(
                        "Skipping RAG cache write for empty or degraded retrieval"
                    )

            latency_breakdown["retrieval_ms"] = int(
                (time.monotonic() - retrieval_started_at) * 1000
            )
            progress_counts = _stream_source_counts(
                chunks=chunks,
                cloud_hits=cloud_hits,
                has_matter_context=bool(
                    matter_context_str and matter_context_str.strip()
                ),
                attachment_count=attachment_count,
            )
            source_previews = _stream_source_previews(chunks, cloud_hits)
            firm_previews, authority_previews = _partition_stream_source_previews(
                source_previews
            )
            yield _stream_activity_event(
                "firm_search",
                "completed",
                (
                    "Firm knowledge search complete"
                    if not cache_hit_rag
                    else "Retrieved sources restored from cache"
                ),
                elapsed_ms=latency_breakdown["retrieval_ms"],
                counts=progress_counts,
                sources=firm_previews,
            )
            if body.include_public:
                yield _stream_activity_event(
                    "public_authority",
                    "completed",
                    "Public authority search complete",
                    elapsed_ms=latency_breakdown["retrieval_ms"],
                    counts=progress_counts,
                    sources=authority_previews,
                )

            context_str = _join_context_sections(
                attachment_context,
                matter_context_str,
                context_str,
            )
            context_str = prepare_provider_text(context_str, privacy_mode)
            memory_context = prepare_provider_text(memory_context, privacy_mode)
            tenant_name = (
                "Legal"
                if public_general
                else prepare_provider_text(
                    user.tenant.name if user.tenant else "Legal", privacy_mode
                )
            )
            generation_started_at = time.monotonic()
            drafting_label = (
                "Drafting a cited answer"
                if progress_counts["total"]
                else "Drafting answer"
            )
            yield _stream_activity_event(
                "drafting",
                "started",
                drafting_label,
                counts=progress_counts,
                sources=source_previews,
            )

            # Stream tokens from the LLM
            accumulated_text = ""
            stream_usage: dict = {}
            last_generation_update = generation_started_at
            async for token in llm_service.stream_complete(
                messages=llm_messages,
                tenant_name=tenant_name,
                context=context_str,
                memory_context=memory_context,
                global_user_context=stream_global_user_context,
                use_premium=use_premium,
                provider=route.provider,
                model=route.model,
                user_name=stream_user_first_name,
                customer_api_key=route.customer_api_key,
                customer_provider=route.customer_provider,
                customer_endpoint=route.customer_endpoint,
                gateway_metadata=gateway_metadata(
                    tenant_id=user.tenant_id,
                    user_id=user.id,
                    conversation_id=conv.id,
                    operation_type="chat_stream",
                    matter_id=None if public_general else effective_matter_id,
                    skill=None
                    if public_general
                    else (body.skill if hasattr(body, "skill") else user.default_skill),
                    premium=use_premium,
                ),
                system_prompt_override=(
                    llm_service.public_general_system_prompt()
                    if public_general
                    else None
                ),
                usage_sink=stream_usage,
            ):
                accumulated_text += token
                now = time.monotonic()
                if now - last_generation_update >= 0.75:
                    yield _stream_activity_event(
                        "drafting",
                        "progress",
                        drafting_label,
                        elapsed_ms=int((now - generation_started_at) * 1000),
                        counts=progress_counts,
                        detail=f"{len(accumulated_text.split())} draft words received",
                    )
                    last_generation_update = now

            latency_breakdown["generation_ms"] = int(
                (time.monotonic() - generation_started_at) * 1000
            )
            yield _stream_activity_event(
                "drafting",
                "completed",
                "Draft complete",
                elapsed_ms=latency_breakdown["generation_ms"],
                counts=progress_counts,
            )
            validation_started_at = time.monotonic()
            yield _stream_activity_event(
                "citation_check",
                "started",
                "Checking citations, source tags, and privacy",
                counts=progress_counts,
            )

            # Apply guardrails to full response
            cleaned_response, needs_retry, response_pii = apply_guardrails(
                accumulated_text, privacy_mode=privacy_mode
            )

            if needs_retry:
                # Clear and retry with explicit instruction
                yield _stream_activity_event(
                    "citation_check",
                    "progress",
                    "Revising the draft before release",
                    counts=progress_counts,
                    detail="The first draft did not pass the response policy check",
                )
                retry_messages = llm_messages + [
                    {
                        "role": "assistant",
                        "content": "I need to revise my response to focus on legal analysis.",
                    },
                ]
                accumulated_text = ""
                retry_usage: dict = {}
                async for token in llm_service.stream_complete(
                    messages=retry_messages,
                    tenant_name=tenant_name,
                    context=context_str,
                    memory_context=memory_context,
                    global_user_context=stream_global_user_context,
                    use_premium=use_premium,
                    provider=route.provider,
                    model=route.model,
                    user_name=stream_user_first_name,
                    customer_api_key=route.customer_api_key,
                    customer_provider=route.customer_provider,
                    customer_endpoint=route.customer_endpoint,
                    gateway_metadata=gateway_metadata(
                        tenant_id=user.tenant_id,
                        user_id=user.id,
                        conversation_id=conv.id,
                        operation_type="chat_stream_retry",
                        matter_id=None if public_general else effective_matter_id,
                        skill=None
                        if public_general
                        else (
                            body.skill if hasattr(body, "skill") else user.default_skill
                        ),
                        premium=use_premium,
                    ),
                    system_prompt_override=(
                        llm_service.public_general_system_prompt()
                        if public_general
                        else None
                    ),
                    usage_sink=retry_usage,
                ):
                    accumulated_text += token

                stream_usage["tokens_in"] = int(
                    stream_usage.get("tokens_in") or 0
                ) + int(retry_usage.get("tokens_in") or 0)
                stream_usage["tokens_out"] = int(
                    stream_usage.get("tokens_out") or 0
                ) + int(retry_usage.get("tokens_out") or 0)
                if retry_usage.get("model"):
                    stream_usage["model"] = retry_usage["model"]

                cleaned_response, _, response_pii = apply_guardrails(
                    accumulated_text, privacy_mode=privacy_mode
                )

            # Build source citations from chunks
            source_dicts = list(attachment_sources)
            context_used = [
                source["source_id"]
                for source in attachment_sources
                if source.get("source_id")
            ]
            context_scores = {source_id: 1.0 for source_id in context_used}
            seen_citations: dict[str, str] = {}
            source_aliases: dict[str, str] = {}
            for source in attachment_sources:
                source_id = source.get("source_id")
                for citation_key in (source.get("citation"), source.get("case_name")):
                    if source_id and citation_key:
                        seen_citations[str(citation_key)] = str(source_id)
            for chunk in chunks:
                chunk_id = str(chunk.get("id", f"chunk_{len(context_used)}"))
                citation_key = chunk.get("citation") or chunk.get("case_name") or ""
                if citation_key and citation_key in seen_citations:
                    source_aliases[chunk_id] = seen_citations[citation_key]
                    continue
                if citation_key:
                    seen_citations[citation_key] = chunk_id
                context_used.append(chunk_id)
                context_scores[chunk_id] = chunk.get("relevance_score", 0.5)
                source_dicts.append(_source_dict_from_chunk(chunk))

            for hit in cloud_hits:
                hit_dict = _cloud_hit_dict(hit)
                cloud_id = _cloud_hit_context_id(hit_dict)
                if cloud_id in seen_citations:
                    continue
                seen_citations[cloud_id] = cloud_id
                context_used.append(cloud_id)
                context_scores[cloud_id] = hit_dict.get("relevance_score", 0.5)
                source_dicts.append(_source_dict_from_cloud_hit(hit_dict))

            cleaned_response = _canonicalize_source_references(
                cleaned_response, source_aliases
            )
            cleaned_response, _ = reconcile_retrieved_source_attribution(
                cleaned_response, source_dicts
            )
            cleaned_response, _ = consolidate_unverified_model_knowledge(
                cleaned_response, source_dicts
            )
            cleaned_response, _ = validate_citation_confidence(
                cleaned_response,
                _citation_validation_sources(source_dicts, chunks, cloud_hits),
            )
            cleaned_response, citation_gap = enforce_legal_citation_integrity(
                body.content,
                cleaned_response,
                source_dicts,
            )
            if citation_gap:
                logger.warning(
                    "Blocked unsupported streamed legal-research answer conversation_id=%s",
                    conv.id,
                )
            source_dicts = _mark_cited_sources(cleaned_response, source_dicts)
            latency_breakdown["validation_ms"] = int(
                (time.monotonic() - validation_started_at) * 1000
            )
            latency_breakdown["response_ready_ms"] = int(
                (time.monotonic() - stream_started_at) * 1000
            )
            yield _stream_activity_event(
                "citation_check",
                "completed",
                "Citations and privacy checks complete",
                elapsed_ms=latency_breakdown["validation_ms"],
                counts=progress_counts,
                sources=source_previews,
            )
            yield _stream_progress_event(
                "citation_metadata",
                {
                    "sources": [
                        source for source in source_dicts if source.get("cited")
                    ],
                    "citation_annotations": build_citation_annotations(
                        cleaned_response, source_dicts
                    ),
                },
            )
            # Extract document artifacts before streaming so the raw
            # :::artifact fence syntax is never sent to the client.
            try:
                stream_extracted = extract_artifacts(cleaned_response)
                stream_visible_response = (
                    strip_artifacts(cleaned_response)
                    if stream_extracted
                    else cleaned_response
                )
            except Exception:
                logger.warning("Artifact extraction failed", exc_info=True)
                stream_extracted = []
                stream_visible_response = cleaned_response

            # Do not expose unvalidated provider output.  Buffering preserves
            # the SSE contract while ensuring the client only receives the
            # privacy- and citation-checked answer.
            for offset in range(0, len(stream_visible_response), 80):
                safe_chunk = stream_visible_response[offset : offset + 80]
                yield _stream_token_event(safe_chunk)

            # Track matter context usage if provided
            if context_matter_id:
                context_used.insert(0, f"matter:{context_matter_id}")
                context_scores[f"matter:{context_matter_id}"] = 1.0

            # Combine PII findings
            all_pii_flags = []
            if pii_findings:
                all_pii_flags.extend([{"source": "input", **pf} for pf in pii_findings])
            if response_pii:
                all_pii_flags.extend(
                    [{"source": "response", **pf} for pf in response_pii]
                )
            if matter_pii_findings:
                all_pii_flags.extend(
                    [{"source": "matter_context", **pf} for pf in matter_pii_findings]
                )

            # Save assistant message
            model_used = str(stream_usage.get("model") or route.model)
            skill_applied = (
                body.skill
                if hasattr(body, "skill") and body.skill
                else user.default_skill
            )
            await set_tenant_context(db, str(user.tenant_id))

            # Mirrors send_message. The action pass runs after the answer has
            # already streamed, so a proposal never delays the first token.
            proposed_actions, action_note = (
                ([], "")
                if public_general
                else await _propose_followthrough_actions(
                    db,
                    user,
                    question=body.content,
                    answer=cleaned_response,
                    rag_context=context_str,
                    route=route,
                    conversation_id=conv.id,
                    use_premium=use_premium,
                    sources=source_dicts,
                )
            )
            if action_note:
                # Persist the same text that will be streamed after commit so a
                # reload can never disagree with the live turn.
                cleaned_response = f"{cleaned_response}{action_note}"
                stream_visible_response = f"{stream_visible_response}{action_note}"

            assistant_msg = Message(
                id=uuid.uuid4(),
                tenant_id=user.tenant_id,
                conversation_id=conv.id,
                role="assistant",
                content=stream_visible_response,
                sources=source_dicts if source_dicts else None,
                skill_applied=skill_applied,
                context_used=context_used if context_used else None,
                context_relevance_scores=context_scores if context_scores else None,
                pii_flags=all_pii_flags if all_pii_flags else None,
                proposed_actions=proposed_actions or None,
            )
            db.add(assistant_msg)

            # Persist extracted artifacts linked to this message. Reuse the
            # pre-stream extraction so the client and DB agree on the content.
            msg_artifacts: list[ChatArtifact] = []
            if stream_extracted:
                try:
                    async with db.begin_nested():
                        for item in stream_extracted:
                            artifact = ChatArtifact(
                                id=uuid.uuid4(),
                                tenant_id=user.tenant_id,
                                conversation_id=conv.id,
                                message_id=assistant_msg.id,
                                created_by_user_id=user.id,
                                title=item.title,
                                content=item.content,
                                format="markdown",
                                matter_id=conv.matter_id,
                            )
                            db.add(artifact)
                            msg_artifacts.append(artifact)
                        await db.flush()
                except Exception:
                    logger.warning("Artifact persistence failed", exc_info=True)
                    msg_artifacts = []

            # Update conversation timestamp
            conv.updated_at = datetime.now(timezone.utc)

            # LiteLLM returns exact usage on the final streaming chunk. BYOK
            # providers without that extension use an estimate of the complete
            # provider input, rather than only the final user message.
            estimated_provider_input = (
                tenant_name
                + context_str
                + memory_context
                + " ".join(
                    str(message.get("content") or "") for message in llm_messages
                )
            )
            tokens_in = int(
                stream_usage.get("tokens_in")
                or len(estimated_provider_input.split()) * 1.3
            )
            tokens_out = int(
                stream_usage.get("tokens_out") or len(accumulated_text.split()) * 1.3
            )
            # BYOK (customer) routes use the tenant's own provider subscription —
            # the platform does not pay for those tokens, so don't bill for them.
            cost = (
                Decimal("0")
                if route.resolved_route == "customer"
                else calculate_cost(
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    model=model_used,
                    billing_tier=user.tenant.billing_tier if user.tenant else "payg",
                )
            )
            cloud_source_ids = [
                _cloud_hit_context_id(_cloud_hit_dict(hit)) for hit in cloud_hits
            ]
            usage = UsageRecord(
                id=uuid.uuid4(),
                tenant_id=user.tenant_id,
                user_id=user.id,
                conversation_id=conv.id,
                requested_route=route.requested_route,
                resolved_route=route.resolved_route,
                gateway_provider=route.gateway_provider,
                gateway_alias=route.gateway_alias,
                final_model=model_used[:200],
                model_used=model_used[:100],
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_usd=cost,
                operation_type="chat_stream",
                query_text=retained_gateway_query_text(provider_question),
                rag_chunks_retrieved=len(chunks),
                rag_source_ids=[c["id"] for c in chunks if c.get("id")]
                + cloud_source_ids,
                ip_address=request.client.host if request.client else None,
                user_agent=(request.headers.get("user-agent") or "")[:500] or None,
                cache_hit_rag=cache_hit_rag,
                cache_hit_llm=False,  # Streaming bypasses LLM cache
                cache_hit_matter=cache_hit_matter,
                latency_breakdown=latency_breakdown,
            )
            db.add(usage)

            await db.commit()
            assistant_turn_committed = True
            generation_state.assistant_turn_committed = True

            # A proposal is clickable and refers to a real task. Do not expose
            # either the note or card until the task, assistant message, and
            # usage record have committed together; otherwise a fast approval
            # can race an uncommitted task or a disconnect can leave a phantom
            # card that rolls back on the server.
            if action_note:
                yield _stream_token_event(action_note)
            if proposed_actions:
                yield _stream_progress_event(
                    "action_proposal", {"proposed_actions": proposed_actions}
                )
            if rag_cache_task is not None:
                cache_result = await asyncio.gather(
                    rag_cache_task, return_exceptions=True
                )
                if cache_result and isinstance(cache_result[0], BaseException):
                    logger.warning("RAG cache write failed: %s", cache_result[0])

            # Run memory generation after the response body releases the pinned
            # turn connection; it must not consume a second pool slot in-flight.
            if not public_general:
                background_tasks.add_task(
                    _trigger_auto_memory_generation_bg,
                    user_id=str(user.id),
                    tenant_id=str(user.tenant_id),
                    conversation_id=str(conv.id),
                    tenant_name=user.tenant.name if user.tenant else "Legal",
                    privacy_mode=privacy_mode,
                )

            # Notify the client of any document artifacts created this message
            if msg_artifacts:
                yield _stream_artifacts_event(msg_artifacts)

            # Send completion marker
            yield "data: [STREAM_COMPLETE]\n\n"

        except Exception as stream_exc:
            logger.exception(
                "Streaming chat failed tenant_id=%s user_id=%s conversation_id=%s",
                stream_tenant_id,
                stream_user_id,
                stream_conversation_id,
            )
            await db.rollback()
            await set_tenant_context(db, str(stream_tenant_id))
            await capture_chat_error(
                db=db,
                error_type="stream_chat_error",
                message=f"Streaming chat failed: {stream_exc}",
                user_id=stream_user_id,
                tenant_id=stream_tenant_id,
                request=request,
                query_text=provider_question,
                conversation_id=stream_conversation_id,
                severity="error",
            )
            terminal_failure_persisted = await _await_critical_cleanup(
                _persist_interrupted_assistant_turn(
                    db=db,
                    tenant_id=stream_tenant_id,
                    user_id=stream_user_id,
                    conversation_id=stream_conversation_id,
                    user_message_id=stream_user_message_id,
                )
            )
            if terminal_failure_persisted:
                generation_state.assistant_turn_committed = True
            yield _stream_error_event(
                "Assistant service temporarily unavailable. Retry this message."
            )
        finally:
            if not assistant_turn_committed and not terminal_failure_persisted:
                # Client cancellation/navigation raises outside ``Exception``
                # on modern Python. Roll back any tool-created task/source
                # promotion before the independent recovery write; otherwise
                # committing the interruption marker would also commit an
                # unseen proposal from the abandoned turn.
                terminal_failure_persisted = (
                    await _close_interrupted_generation_if_needed(
                        db,
                        generation_state,
                    )
                )

    return StreamingResponse(
        stream_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


async def _error_stream(message: str):
    """Generate SSE error response."""
    yield _stream_error_event(message)
