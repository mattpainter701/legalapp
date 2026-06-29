import asyncio
import html
import json
import logging
import os
import re
import shutil
import uuid
from datetime import date, datetime, timedelta, timezone
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
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.models.conversation import Conversation, Message, UsageRecord
from app.models.document import Document
from app.models.plugin import Matter as MatterModel
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
from app.services.embeddings import EmbeddingService
from app.services.rag import hybrid_rag_query
from app.services.llm import LLMService
from app.services.llm_routing import (
    classify_query_complexity,
    resolve_llm_route,
)
from app.services.billing import calculate_cost
from app.services.memory_service import MemoryService
from app.services.matter_context import MatterContextService
from app.services.cache import ExpertiseCacheManager
from app.services.gateway_privacy import gateway_metadata, retained_gateway_query_text
from app.utils.guardrails import apply_guardrails, check_pii_in_input
from app.services.error_tracker import capture_chat_error

logger = logging.getLogger(__name__)

settings = get_settings()

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_ANCHOR_TEXT_RE = re.compile(r"<a\b[^>]*>(.*?)</a>", re.IGNORECASE | re.DOTALL)
_HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)


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


def _normalize_source_url(value: str | None) -> str | None:
    if not value:
        return None
    url = html.unescape(str(value).strip())
    if not url:
        return None
    if url.startswith("//"):
        return f"https:{url}"
    if url.startswith("/"):
        return f"https://www.courtlistener.com{url}"
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return None


def _source_url_from_chunk(chunk: dict) -> str | None:
    for key in ("url", "source_url", "absolute_url"):
        url = _normalize_source_url(chunk.get(key))
        if url:
            return url
    opinion_id = chunk.get("opinion_id")
    if opinion_id:
        return f"https://www.courtlistener.com/opinion/{opinion_id}/"
    for key in ("citation", "content", "excerpt"):
        raw = chunk.get(key)
        if not raw:
            continue
        href_match = _HREF_RE.search(str(raw))
        if href_match:
            url = _normalize_source_url(href_match.group(1))
            if url:
                return url
    return None


def _source_label(source_type: str | None) -> str:
    labels = {
        "public_authority": "Cited authority",
        "courtlistener_mcp": "Cited authority",
        "cloud": "Cloud context",
        "matter_context": "Matter context",
        "tenant_document": "Firm context",
    }
    return labels.get(source_type or "", "Context")


def _source_dict_from_chunk(chunk: dict) -> dict:
    source_type = chunk.get("clause_type") or chunk.get("source") or "public_authority"
    if source_type == "courtlistener_mcp":
        source_type = "public_authority"
    return {
        "case_name": _clean_source_text(chunk.get("case_name"), 180) or "Unknown Case",
        "citation": _clean_source_text(chunk.get("citation"), 120),
        "court": _clean_source_text(chunk.get("court"), 120) or None,
        "excerpt": _clean_source_text(chunk.get("content") or chunk.get("excerpt"), 300),
        "url": _source_url_from_chunk(chunk),
        "source_type": source_type,
        "source_label": _source_label(source_type),
    }


def _source_dict_from_cloud_hit(hit_dict: dict) -> dict:
    source_type = "cloud"
    cloud_id = _cloud_hit_context_id(hit_dict)
    return {
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
    }


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
                status_code=400,
                detail="Message matter does not match linked conversation matter",
            )
        return matter

    if conv.matter_id:
        return await _matter_for_tenant_or_400(db, user, conv.matter_id)
    return None


def _rag_scope_key(matter_id: str | None, matter_cloud_folder: dict | None) -> str:
    return json.dumps(
        {
            "matter_id": matter_id or "none",
            "matter_cloud_folder": matter_cloud_folder or None,
        },
        sort_keys=True,
        default=str,
    )


def _cloud_hit_dict(hit) -> dict:
    return hit.to_dict() if hasattr(hit, "to_dict") else dict(hit)


def _cloud_hit_context_id(hit_dict: dict) -> str:
    return (
        f"cloud:{hit_dict.get('provider')}:{hit_dict.get('source')}:"
        f"{hit_dict.get('object_id')}"
    )


async def _build_attachment_context(
    db: AsyncSession,
    user,
    conversation: Conversation,
    attachment_ids: list[str] | None,
) -> str:
    """Inject session-attachment text directly into context (Tier 1 — no embeddings).

    For documents that were chunked/embedded (chunk_count > 0), use the stored
    chunks. Otherwise extract text on-demand from the file on disk.
    """
    if not attachment_ids:
        return ""
    try:
        from app.utils.text_processing import extract_text as _extract_text

        attachment_parts = []
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
                attachment_parts.append(
                    f"[Attachment: {doc.filename or 'Untitled'}]\n{text[:4000]}"
                )

        if attachment_parts:
            return (
                "--- Attached Files (session-only, not saved to project) ---\n\n"
                + "\n\n---\n\n".join(attachment_parts)
            )
    except Exception:
        logger.warning("Failed to build attachment context", exc_info=True)
    return ""


def _conversation_to_response(
    conv: Conversation, message_count: int = None
) -> ConversationResponse:
    return ConversationResponse(
        id=str(conv.id),
        title=conv.title,
        matter_id=str(conv.matter_id) if conv.matter_id else None,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        message_count=message_count,
    )


def _message_to_response(msg: Message) -> MessageResponse:
    sources = []
    if msg.sources:
        for s in msg.sources:
            source_type = s.get("source_type") or (
                "public_authority" if s.get("case_name") or s.get("court") else None
            )
            sources.append(
                SourceCitation(
                    case_name=_clean_source_text(s.get("case_name"), 180) or "Unknown",
                    citation=_clean_source_text(s.get("citation"), 120),
                    court=_clean_source_text(s.get("court"), 120) or None,
                    excerpt=_clean_source_text(s.get("excerpt"), 300),
                    url=_source_url_from_chunk(s),
                    source_type=source_type,
                    source_label=s.get("source_label") or _source_label(source_type),
                )
            )
    return MessageResponse(
        id=str(msg.id),
        conversation_id=str(msg.conversation_id),
        role=msg.role,
        content=msg.content,
        sources=sources,
        created_at=msg.created_at,
    )


def _conversation_belongs_to_user(conv: Conversation, user) -> bool:
    """Chat conversations are private to their creator, including tenant admins."""
    return str(conv.user_id) == str(user.id)


async def _trigger_auto_memory_generation_bg(
    user_id: str,
    tenant_id: str,
    conversation_id: str,
    tenant_name: str,
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


async def _check_token_budget(db: AsyncSession, user) -> None:
    """Raise HTTP 429 if the tenant has exhausted their daily token budget."""
    from app.models.tenant import TenantSettings

    settings_result = await db.execute(
        select(TenantSettings).where(TenantSettings.tenant_id == user.tenant_id)
    )
    tenant_settings = settings_result.scalar_one_or_none()
    if not tenant_settings or not tenant_settings.max_daily_tokens:
        return

    today_start = datetime.combine(date.today(), datetime.min.time()).replace(
        tzinfo=timezone.utc
    )
    token_result = await db.execute(
        select(
            func.coalesce(func.sum(UsageRecord.tokens_in + UsageRecord.tokens_out), 0)
        ).where(
            UsageRecord.tenant_id == user.tenant_id,
            UsageRecord.created_at >= today_start,
        )
    )
    tokens_today = token_result.scalar() or 0
    if tokens_today >= tenant_settings.max_daily_tokens:
        raise HTTPException(
            status_code=429,
            detail="Daily token limit reached. Contact your administrator.",
        )


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

    # Get message counts efficiently
    count_result = await db.execute(
        select(Message.conversation_id, func.count(Message.id).label("cnt"))
        .where(Message.conversation_id.in_([c.id for c in conversations]))
        .group_by(Message.conversation_id)
    )
    count_map = {str(row.conversation_id): row.cnt for row in count_result.fetchall()}

    return [
        _conversation_to_response(c, count_map.get(str(c.id), 0)) for c in conversations
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
        .order_by(Message.created_at.asc())
    )
    messages = msg_result.scalars().all()

    return ConversationDetail(
        conversation=_conversation_to_response(conv, len(messages)),
        messages=[_message_to_response(m) for m in messages],
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

    if body.title is not None:
        title = body.title.strip()
        if not title:
            raise HTTPException(status_code=400, detail="Conversation title is required")
        if len(title) > 500:
            raise HTTPException(
                status_code=400, detail="Conversation title must be 500 characters or less"
            )
        conv.title = title

    if body.matter_id is not None:
        matter_id = body.matter_id.strip()
        if not matter_id:
            conv.matter_id = None
        else:
            matter = await _matter_for_tenant_or_400(db, user, matter_id)
            conv.matter_id = matter.id

    if body.title is None and body.matter_id is None:
        raise HTTPException(status_code=400, detail="No conversation updates provided")

    count_result = await db.execute(
        select(func.count(Message.id)).where(Message.conversation_id == conv.id)
    )
    message_count = count_result.scalar() or 0
    await db.commit()
    return _conversation_to_response(conv, message_count)


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
        matter_result = await db.execute(
            select(MatterModel.slug).where(
                MatterModel.id == conv.matter_id,
                MatterModel.tenant_id == user.tenant_id,
            )
        )
        matter_row = matter_result.one_or_none()
        matter_slug = matter_row[0] if matter_row else str(conv.matter_id)
        storage_dir = os.path.join(
            settings.UPLOAD_DIR,
            str(user.tenant_id),
            "matters",
            matter_slug,
            "chatattachments",
            str(document_id),
        )
    else:
        storage_dir = os.path.join(
            settings.UPLOAD_DIR,
            str(user.tenant_id),
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
        tenant_id=user.tenant_id,
        user_id=user.id,
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
    db.add(doc)
    await db.flush()
    await db.commit()

    return ChatAttachmentResponse.model_validate(doc)


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

    # Remove attachment files from disk before the FK cascade deletes their rows.
    attachment_result = await db.execute(
        select(Document.storage_path).where(Document.conversation_id == conv.id)
    )
    for (storage_path,) in attachment_result.all():
        if storage_path:
            shutil.rmtree(os.path.dirname(storage_path), ignore_errors=True)

    await db.delete(conv)
    await db.commit()


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

    effective_matter = await _effective_message_matter(db, user, conv, body)
    effective_matter_id = str(effective_matter.id) if effective_matter else None
    effective_matter_cloud_folder = (
        effective_matter.cloud_folder if effective_matter else None
    )
    rag_scope_key = _rag_scope_key(effective_matter_id, effective_matter_cloud_folder)

    # 1a. Enforce daily token budget (fail fast before any work is done)
    await _check_token_budget(db, user)

    # 2. Check for PII in user input
    pii_findings = check_pii_in_input(body.content) if user.privacy_mode else []

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

    # 3. Load recent conversation history (last 10 messages)
    history_result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conv.id)
        .order_by(Message.created_at.desc())
        .limit(11)  # 10 + the one we just added
    )
    recent_messages = list(reversed(history_result.scalars().all()))
    # Exclude the message we just added (it's the last one)
    history_messages = [
        {"role": m.role, "content": m.content}
        for m in recent_messages
        if str(m.id) != str(user_msg.id)
    ][-10:]
    llm_messages = history_messages + [{"role": "user", "content": body.content}]

    # 3a. Fire all pre-work in parallel: matter context, memory context, LLM route,
    #     attachment context, and RAG cache check. These are independent reads.
    async def _load_matter_context_nonstream():
        if not effective_matter_id:
            return "", [], None, False

        cached = await cache_manager.get_cached_matter_context(
            matter_id=effective_matter_id,
            tenant_id=str(user.tenant_id),
        )
        if cached:
            return cached, [], effective_matter_cloud_folder, True
        mcs, has_pii, mpf = await matter_context_service.get_safe_matter_context(
            db=db,
            matter_id=effective_matter_id,
            tenant_id=user.tenant_id,
            privacy_mode=user.privacy_mode,
        )
        await cache_manager.set_cached_matter_context(
            matter_id=effective_matter_id,
            tenant_id=str(user.tenant_id),
            context=mcs,
            expertise_level=user.expertise_level,
        )
        return mcs, mpf if mpf else [], effective_matter_cloud_folder, False

    (
        (
            matter_context_str,
            matter_pii_findings,
            matter_cloud_folder,
            cache_hit_matter,
        ),
        attachment_context,
        memory_context,
        route,
        cached_rag,
    ) = await asyncio.gather(
        _load_matter_context_nonstream(),
        _build_attachment_context(
            db,
            user,
            conv,
            body.attachment_ids if hasattr(body, "attachment_ids") else None,
        ),
        memory_service.get_memory_context_for_injection(db=db, user_id=user.id),
        resolve_llm_route(
            db,
            user.tenant_id,
            use_premium=_premium_for_user(user, body.content, body.use_premium_llm),
            requested_provider=body.provider,
        ),
        cache_manager.get_cached_rag_results(
            question=body.content,
            tenant_id=str(user.tenant_id),
            user_id=str(user.id),
            skill=body.skill if hasattr(body, "skill") else user.default_skill,
            include_public=body.include_public,
            scope_key=rag_scope_key,
        ),
    )

    # 4. RAG: embed question, search chunks, build context (with caching)
    cache_hit_rag = False
    cloud_hits = []
    if cached_rag:
        context_str, chunks = cached_rag
        cache_hit_rag = True
    else:
        try:
            context_str, chunks, cloud_hits = await hybrid_rag_query(
                db=db,
                embedding_service=embedding_service,
                question=body.content,
                tenant_id=str(user.tenant_id),
                user_id=str(user.id),
                include_public=body.include_public,
                cloud_search_service=_get_cloud_search_service(),
                retrieval_planner=_get_retrieval_planner(),
                tenant_name=user.tenant.name if user.tenant else "Legal",
                matter_context_str=matter_context_str,
                matter_id=effective_matter_id,
                matter_cloud_folder=matter_cloud_folder,
            )
            # Cache RAG results
            await cache_manager.set_cached_rag_results(
                question=body.content,
                tenant_id=str(user.tenant_id),
                user_id=str(user.id),
                context_str=context_str,
                chunks=chunks,
                expertise_level=user.expertise_level,
                skill=body.skill if hasattr(body, "skill") else user.default_skill,
                include_public=body.include_public,
                scope_key=rag_scope_key,
            )
        except Exception as rag_exc:
            logger.exception("RAG query failed")
            await capture_chat_error(
                db=db,
                error_type="rag_query_error",
                message=f"RAG query failed: {rag_exc}",
                user_id=user.id,
                tenant_id=user.tenant_id,
                request=request,
                query_text=body.content,
                conversation_id=conv.id,
                severity="error",
            )
            context_str, chunks = "", []

    # 4a. Combine attachment, matter, and RAG context
    context_str = _join_context_sections(
        attachment_context,
        matter_context_str,
        context_str,
    )

    # 5. Call LLM (with caching)
    import hashlib

    tenant_name = user.tenant.name if user.tenant else "Legal"
    user_first_name = (user.full_name or "").split()[0] if user.full_name else ""

    context_hash = hashlib.md5(f"{route.cache_key}\n{context_str}".encode()).hexdigest()

    cache_hit_llm = False
    cached_response = await cache_manager.get_cached_llm_response(
        question=body.content,
        tenant_id=str(user.tenant_id),
        user_id=str(user.id),
        context_hash=context_hash,
        skill=body.skill if hasattr(body, "skill") else user.default_skill,
    )

    tokens_in, tokens_out = 0, 0
    if cached_response:
        response_text = cached_response
        cache_hit_llm = True
    else:
        try:
            response_text, tokens_in, tokens_out = await llm_service.complete(
                messages=llm_messages,
                tenant_name=tenant_name,
                context=context_str,
                memory_context=memory_context,
                use_premium=_premium_for_user(user, body.content, body.use_premium_llm),
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
                    matter_id=effective_matter_id,
                    skill=body.skill if hasattr(body, "skill") else user.default_skill,
                    premium=_premium_for_user(user, body.content, body.use_premium_llm),
                ),
            )
            # Cache LLM response
            await cache_manager.set_cached_llm_response(
                question=body.content,
                tenant_id=str(user.tenant_id),
                user_id=str(user.id),
                context_hash=context_hash,
                response=response_text,
                expertise_level=user.expertise_level,
                skill=body.skill if hasattr(body, "skill") else user.default_skill,
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
                query_text=body.content,
                conversation_id=conv.id,
                severity="error",
            )
            raise HTTPException(
                status_code=502, detail="LLM service temporarily unavailable"
            )

    # 6. Apply guardrails (check for AI self-disclosure and PII in privacy mode)
    cleaned_response, needs_retry, response_pii = apply_guardrails(
        response_text, privacy_mode=user.privacy_mode
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
            use_premium=_premium_for_user(user, body.content, body.use_premium_llm),
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
                matter_id=effective_matter_id,
                skill=body.skill if hasattr(body, "skill") else user.default_skill,
                premium=_premium_for_user(user, body.content, body.use_premium_llm),
            ),
        )
        cleaned_response, _, response_pii = apply_guardrails(
            response_text2, privacy_mode=user.privacy_mode
        )
        tokens_in += tokens_in2
        tokens_out += tokens_out2

    # Build source citations from retrieved chunks with relevance scores
    source_dicts = []
    context_used = []
    context_scores = {}
    seen_citations: set = set()
    for chunk in chunks:
        citation_key = chunk.get("citation") or chunk.get("case_name") or ""
        if citation_key and citation_key in seen_citations:
            continue
        if citation_key:
            seen_citations.add(citation_key)
        chunk_id = chunk.get("id", f"chunk_{len(context_used)}")
        context_used.append(chunk_id)
        context_scores[chunk_id] = chunk.get("relevance_score", 0.5)
        source_dicts.append(_source_dict_from_chunk(chunk))

    for hit in cloud_hits:
        hit_dict = _cloud_hit_dict(hit)
        cloud_id = _cloud_hit_context_id(hit_dict)
        if cloud_id in seen_citations:
            continue
        seen_citations.add(cloud_id)
        context_used.append(cloud_id)
        context_scores[cloud_id] = hit_dict.get("relevance_score", 0.5)
        source_dicts.append(_source_dict_from_cloud_hit(hit_dict))

    # Track matter context usage if provided
    if effective_matter_id:
        context_used.insert(0, f"matter:{effective_matter_id}")
        context_scores[f"matter:{effective_matter_id}"] = 1.0

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
    model_used = route.model
    skill_applied = (
        body.skill if hasattr(body, "skill") and body.skill else user.default_skill
    )
    await set_tenant_context(db, str(user.tenant_id))
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
    )
    db.add(assistant_msg)

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
        final_model=route.gateway_alias,
        model_used=model_used,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost,
        operation_type="chat",
        query_text=retained_gateway_query_text(body.content),
        rag_chunks_retrieved=len(chunks),
        rag_source_ids=[c["id"] for c in chunks if c.get("id")]
        + cloud_source_ids,
        ip_address=request.client.host if request.client else None,
        user_agent=(request.headers.get("user-agent") or "")[:500] or None,
        cache_hit_rag=cache_hit_rag,
        cache_hit_llm=cache_hit_llm,
        cache_hit_matter=cache_hit_matter,
    )
    db.add(usage)

    await db.flush()
    await db.commit()

    # Trigger auto-memory generation in background (non-blocking)
    background_tasks.add_task(
        _trigger_auto_memory_generation_bg,
        user_id=str(user.id),
        tenant_id=str(user.tenant_id),
        conversation_id=str(conv.id),
        tenant_name=user.tenant.name if user.tenant else "Legal",
    )

    return _message_to_response(assistant_msg)


@router.post("/{conversation_id}/messages/stream")
async def stream_message(
    conversation_id: str,
    body: MessageCreate,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """
    Streaming RAG endpoint — same as send_message but returns SSE stream of tokens.
    Yields tokens as they arrive from the LLM, prefixed with 'data: '.
    Sends [STREAM_COMPLETE] when done, or [ERROR: message] on failure.
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
        effective_matter = await _effective_message_matter(db, user, conv, body)
    except HTTPException as matter_exc:
        return StreamingResponse(
            _error_stream(matter_exc.detail),
            media_type="text/event-stream",
        )
    effective_matter_id = str(effective_matter.id) if effective_matter else None
    effective_matter_cloud_folder = (
        effective_matter.cloud_folder if effective_matter else None
    )
    rag_scope_key = _rag_scope_key(effective_matter_id, effective_matter_cloud_folder)

    # 1a. Enforce daily token budget
    try:
        await _check_token_budget(db, user)
    except HTTPException as budget_exc:
        return StreamingResponse(
            _error_stream(budget_exc.detail),
            media_type="text/event-stream",
        )

    # 2. Check for PII in user input
    pii_findings = check_pii_in_input(body.content) if user.privacy_mode else []

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

    # 3. Load recent conversation history (last 10 messages)
    history_result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conv.id)
        .order_by(Message.created_at.desc())
        .limit(11)
    )
    recent_messages = list(reversed(history_result.scalars().all()))
    history_messages = [
        {"role": m.role, "content": m.content}
        for m in recent_messages
        if str(m.id) != str(user_msg.id)
    ][-10:]
    llm_messages = history_messages + [{"role": "user", "content": body.content}]

    # 3a. Fire all pre-work in parallel: matter context, memory context, LLM route,
    #     attachment context, and RAG cache check. These are independent reads.
    async def _load_matter_context():
        if not effective_matter_id:
            return "", [], None, False

        cached = await cache_manager.get_cached_matter_context(
            matter_id=effective_matter_id,
            tenant_id=str(user.tenant_id),
        )
        if cached:
            return cached, [], effective_matter_cloud_folder, True
        mcs, has_pii, mpf = await matter_context_service.get_safe_matter_context(
            db=db,
            matter_id=effective_matter_id,
            tenant_id=user.tenant_id,
            privacy_mode=user.privacy_mode,
        )
        await cache_manager.set_cached_matter_context(
            matter_id=effective_matter_id,
            tenant_id=str(user.tenant_id),
            context=mcs,
            expertise_level=user.expertise_level,
        )
        return mcs, mpf if mpf else [], effective_matter_cloud_folder, False

    (
        (
            matter_context_str,
            matter_pii_findings,
            matter_cloud_folder,
            cache_hit_matter,
        ),
        attachment_context,
        memory_context,
        route,
        cached_rag,
    ) = await asyncio.gather(
        _load_matter_context(),
        _build_attachment_context(
            db,
            user,
            conv,
            body.attachment_ids if hasattr(body, "attachment_ids") else None,
        ),
        memory_service.get_memory_context_for_injection(db=db, user_id=user.id),
        resolve_llm_route(
            db,
            user.tenant_id,
            use_premium=_premium_for_user(user, body.content, body.use_premium_llm),
            requested_provider=body.provider,
        ),
        cache_manager.get_cached_rag_results(
            question=body.content,
            tenant_id=str(user.tenant_id),
            user_id=str(user.id),
            skill=body.skill if hasattr(body, "skill") else user.default_skill,
            include_public=body.include_public,
            scope_key=rag_scope_key,
        ),
    )

    # 4. RAG: embed question, search chunks, build context (with caching)
    cache_hit_rag = False
    cloud_hits = []
    if cached_rag:
        context_str, chunks = cached_rag
        cache_hit_rag = True
    else:
        try:
            context_str, chunks, cloud_hits = await hybrid_rag_query(
                db=db,
                embedding_service=embedding_service,
                question=body.content,
                tenant_id=str(user.tenant_id),
                user_id=str(user.id),
                include_public=body.include_public,
                cloud_search_service=_get_cloud_search_service(),
                retrieval_planner=_get_retrieval_planner(),
                tenant_name=user.tenant.name if user.tenant else "Legal",
                matter_context_str=matter_context_str,
                matter_id=effective_matter_id,
                matter_cloud_folder=matter_cloud_folder,
            )
        except Exception:
            logger.exception(
                "RAG query failed in streaming path, continuing without context"
            )
            context_str, chunks = "", []
        await cache_manager.set_cached_rag_results(
            question=body.content,
            tenant_id=str(user.tenant_id),
            user_id=str(user.id),
            context_str=context_str,
            chunks=chunks,
            expertise_level=user.expertise_level,
            skill=body.skill if hasattr(body, "skill") else user.default_skill,
            include_public=body.include_public,
            scope_key=rag_scope_key,
        )

    # 4a. Combine attachment, matter, and RAG context
    context_str = _join_context_sections(
        attachment_context,
        matter_context_str,
        context_str,
    )

    # Create the streaming generator
    stream_user_first_name = (user.full_name or "").split()[0] if user.full_name else ""

    async def stream_generator():
        try:
            tenant_name = user.tenant.name if user.tenant else "Legal"

            # Stream tokens from the LLM
            accumulated_text = ""
            async for token in llm_service.stream_complete(
                messages=llm_messages,
                tenant_name=tenant_name,
                context=context_str,
                memory_context=memory_context,
                use_premium=_premium_for_user(user, body.content, body.use_premium_llm),
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
                    matter_id=effective_matter_id,
                    skill=body.skill if hasattr(body, "skill") else user.default_skill,
                    premium=_premium_for_user(user, body.content, body.use_premium_llm),
                ),
            ):
                accumulated_text += token
                yield f"data: {token}\n\n"

            # Apply guardrails to full response
            cleaned_response, needs_retry, response_pii = apply_guardrails(
                accumulated_text, privacy_mode=user.privacy_mode
            )

            if needs_retry:
                # Clear and retry with explicit instruction
                retry_messages = llm_messages + [
                    {
                        "role": "assistant",
                        "content": "I need to revise my response to focus on legal analysis.",
                    },
                ]
                accumulated_text = ""
                async for token in llm_service.stream_complete(
                    messages=retry_messages,
                    tenant_name=tenant_name,
                    context=context_str,
                    use_premium=_premium_for_user(user, body.content, body.use_premium_llm),
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
                        matter_id=effective_matter_id,
                        skill=body.skill if hasattr(body, "skill") else user.default_skill,
                        premium=_premium_for_user(user, body.content, body.use_premium_llm),
                    ),
                ):
                    accumulated_text += token
                    yield f"data: {token}\n\n"

                cleaned_response, _, response_pii = apply_guardrails(
                    accumulated_text, privacy_mode=user.privacy_mode
                )

            # Build source citations from chunks
            source_dicts = []
            context_used = []
            context_scores = {}
            seen_citations: set = set()
            for chunk in chunks:
                citation_key = chunk.get("citation") or chunk.get("case_name") or ""
                if citation_key and citation_key in seen_citations:
                    continue
                if citation_key:
                    seen_citations.add(citation_key)
                chunk_id = chunk.get("id", f"chunk_{len(context_used)}")
                context_used.append(chunk_id)
                context_scores[chunk_id] = chunk.get("relevance_score", 0.5)
                source_dicts.append(_source_dict_from_chunk(chunk))

            for hit in cloud_hits:
                hit_dict = _cloud_hit_dict(hit)
                cloud_id = _cloud_hit_context_id(hit_dict)
                if cloud_id in seen_citations:
                    continue
                seen_citations.add(cloud_id)
                context_used.append(cloud_id)
                context_scores[cloud_id] = hit_dict.get("relevance_score", 0.5)
                source_dicts.append(_source_dict_from_cloud_hit(hit_dict))

            # Track matter context usage if provided
            if effective_matter_id:
                context_used.insert(0, f"matter:{effective_matter_id}")
                context_scores[f"matter:{effective_matter_id}"] = 1.0

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
            model_used = route.model
            skill_applied = (
                body.skill
                if hasattr(body, "skill") and body.skill
                else user.default_skill
            )
            await set_tenant_context(db, str(user.tenant_id))
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
            )
            db.add(assistant_msg)

            # Update conversation timestamp
            conv.updated_at = datetime.now(timezone.utc)

            # Record usage (estimated tokens for streaming)
            tokens_in = len(body.content.split()) * 1.3  # Rough estimate
            tokens_out = len(accumulated_text.split()) * 1.3
            # BYOK (customer) routes use the tenant's own provider subscription —
            # the platform does not pay for those tokens, so don't bill for them.
            cost = (
                Decimal("0")
                if route.resolved_route == "customer"
                else calculate_cost(
                    tokens_in=int(tokens_in),
                    tokens_out=int(tokens_out),
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
                final_model=route.gateway_alias,
                model_used=model_used,
                tokens_in=int(tokens_in),
                tokens_out=int(tokens_out),
                cost_usd=cost,
                operation_type="chat_stream",
                query_text=retained_gateway_query_text(body.content),
                rag_chunks_retrieved=len(chunks),
                rag_source_ids=[c["id"] for c in chunks if c.get("id")]
                + cloud_source_ids,
                ip_address=request.client.host if request.client else None,
                user_agent=(request.headers.get("user-agent") or "")[:500] or None,
                cache_hit_rag=cache_hit_rag,
                cache_hit_llm=False,  # Streaming bypasses LLM cache
                cache_hit_matter=cache_hit_matter,
            )
            db.add(usage)

            await db.commit()

            # Fire memory generation without blocking the stream completion
            asyncio.create_task(
                _trigger_auto_memory_generation_bg(
                    user_id=str(user.id),
                    tenant_id=str(user.tenant_id),
                    conversation_id=str(conv.id),
                    tenant_name=user.tenant.name if user.tenant else "Legal",
                )
            )

            # Send completion marker
            yield "data: [STREAM_COMPLETE]\n\n"

        except Exception as e:
            import traceback

            traceback.print_exc()
            yield f"data: [ERROR: {str(e)[:200]}]\n\n"

    return StreamingResponse(stream_generator(), media_type="text/event-stream")


async def _error_stream(message: str):
    """Generate SSE error response."""
    yield f"data: [ERROR: {message}]\n\n"
