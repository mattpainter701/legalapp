import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.models.conversation import Conversation, Message, UsageRecord
from app.models.document import Document
from app.models.user import User
from app.schemas.chat import (
    ConversationCreate,
    ConversationResponse,
    ConversationDetail,
    MessageCreate,
    MessageResponse,
    SourceCitation,
)
from app.services.embeddings import EmbeddingService
from app.services.rag import hybrid_rag_query
from app.services.llm import LLMService
from app.services.billing import calculate_cost
from app.services.memory_service import MemoryService
from app.services.matter_context import MatterContextService
from app.services.cache import ExpertiseCacheManager
from app.utils.guardrails import apply_guardrails, check_pii_in_input
from app.services.error_tracker import capture_chat_error

logger = logging.getLogger(__name__)

settings = get_settings()


async def _safe_cache_op(db, user, request, conv_id, query_text, op_name, coro):
    """Execute a cache operation with error capture on failure (non-fatal)."""
    try:
        return await coro()
    except Exception as cache_exc:
        logger = __import__("logging").getLogger(__name__)
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


def _conversation_to_response(
    conv: Conversation, message_count: int = None
) -> ConversationResponse:
    return ConversationResponse(
        id=str(conv.id),
        title=conv.title,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        message_count=message_count,
    )


def _message_to_response(msg: Message) -> MessageResponse:
    sources = []
    if msg.sources:
        for s in msg.sources:
            sources.append(
                SourceCitation(
                    case_name=s.get("case_name", "Unknown"),
                    citation=s.get("citation", ""),
                    court=s.get("court"),
                    excerpt=s.get("excerpt", ""),
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


async def _trigger_auto_memory_generation(
    db: AsyncSession,
    user: User,
    conversation_id: str,
) -> None:
    """
    Trigger auto-memory generation if conversation has >= 10 messages.
    Extracts conversation summary and key facts into UserMemory.
    """
    # Count messages in conversation
    count_result = await db.execute(
        select(func.count(Message.id)).where(Message.conversation_id == conversation_id)
    )
    message_count = count_result.scalar() or 0

    # Trigger memory generation every 10 messages
    if message_count % 10 == 0 and message_count > 0:
        try:
            await memory_service.summarize_conversation(
                db=db,
                user_id=user.id,
                tenant_id=user.tenant_id,
                conversation_id=conversation_id,
                tenant_name=user.tenant.name if user.tenant else "Legal",
            )
            # Update overall user memory summary
            await memory_service.update_user_memory_summary(
                db=db,
                user_id=user.id,
                tenant_id=user.tenant_id,
                tensor_name=user.tenant.name if user.tenant else "Legal",
            )
            await db.commit()
        except Exception:
            logger.warning("Auto-memory generation failed", exc_info=True)


@router.get("", response_model=List[ConversationResponse])
async def list_conversations(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """List all conversations for the current user."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    result = await db.execute(
        select(Conversation)
        .where(
            Conversation.user_id == user.id,
            Conversation.tenant_id == user.tenant_id,
        )
        .order_by(Conversation.updated_at.desc())
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

    title = body.title or "New Conversation"
    conv = Conversation(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        user_id=user.id,
        title=title,
    )
    db.add(conv)
    await db.commit()
    await db.refresh(conv)

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
        )
    )
    conv = result.scalar_one_or_none()

    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if conv.user_id != user.id and user.role != "admin":
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
        )
    )
    conv = result.scalar_one_or_none()

    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if conv.user_id != user.id and user.role != "admin":
        raise HTTPException(status_code=403, detail="Access denied")

    await db.delete(conv)
    await db.commit()


@router.post(
    "/{conversation_id}/messages", response_model=MessageResponse, status_code=201
)
async def send_message(
    conversation_id: str,
    body: MessageCreate,
    request: Request,
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
        )
    )
    conv = result.scalar_one_or_none()

    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if conv.user_id != user.id and user.role != "admin":
        raise HTTPException(status_code=403, detail="Access denied")

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

    # 3a. Load matter context if provided (with caching)
    matter_context_str = ""
    matter_pii_findings = []
    cache_hit_matter = False
    if hasattr(body, "matter_id") and body.matter_id:
        # Try cache first
        cached_matter = await cache_manager.get_cached_matter_context(
            matter_id=body.matter_id,
            tenant_id=str(user.tenant_id),
        )
        if cached_matter:
            matter_context_str = cached_matter
            cache_hit_matter = True
        else:
            (
                matter_context_str,
                has_pii,
                matter_pii_findings,
            ) = await matter_context_service.get_safe_matter_context(
                db=db,
                matter_id=body.matter_id,
                privacy_mode=user.privacy_mode,
            )
            # Cache matter context
            await cache_manager.set_cached_matter_context(
                matter_id=body.matter_id,
                tenant_id=str(user.tenant_id),
                context=matter_context_str,
                expertise_level=user.expertise_level,
            )

    # 3b. Session attachments — inject file text directly into context (no embeddings)
    attachment_context = ""
    if hasattr(body, "attachment_ids") and body.attachment_ids:
        try:
            from app.utils.text_processing import extract_text as _extract_text

            attachment_parts = []
            for aid in body.attachment_ids:
                doc_result = await db.execute(
                    select(Document).where(
                        Document.id == aid,
                        Document.tenant_id == user.tenant_id,
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
                            "ORDER BY chunk_index LIMIT 100"
                        ),
                        {"doc_id": str(doc.id)},
                    )
                    text = "\n\n".join(row[0] for row in chunk_result.fetchall())
                else:
                    file_path = doc.file_path or (
                        f"{settings.UPLOAD_DIR}/{user.tenant_id}/{doc.id}/{doc.filename}"
                    )
                    text = await asyncio.to_thread(_extract_text, file_path)

                if text:
                    attachment_parts.append(
                        f"[Attachment: {doc.filename or 'Untitled'}]\n{text[:4000]}"
                    )

            if attachment_parts:
                attachment_context = (
                    "--- Attached Files (session-only, not saved to project) ---\n\n"
                    + "\n\n---\n\n".join(attachment_parts)
                )
        except Exception:
            pass  # Non-fatal

    # 4. RAG: embed question, search chunks, build context (with caching)
    cache_hit_rag = False
    cached_rag = await cache_manager.get_cached_rag_results(
        question=body.content,
        tenant_id=str(user.tenant_id),
        user_id=str(user.id),
        skill=body.skill if hasattr(body, "skill") else user.default_skill,
    )

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
            context_str, chunks = "No relevant legal context available.", []

    # 4a. Combine matter context with RAG context
    if matter_context_str:
        context_str = f"{matter_context_str}\n\n{context_str}"

    if attachment_context:
        context_str = f"{attachment_context}\n\n{context_str}"

    # 4b. Load user memory context for injection into system prompt
    memory_context = await memory_service.get_memory_context_for_injection(
        db=db,
        user_id=user.id,
    )

    # 5. Call LLM (with caching)
    import hashlib

    context_hash = hashlib.md5(context_str.encode()).hexdigest()

    tenant_name = user.tenant.name if user.tenant else "Legal"

    # Resolve provider: if "default", use tenant's configured LLM provider/model
    resolved_provider = body.provider
    resolved_model = None
    if body.provider == "default":
        ts_result = await db.execute(
            select(TenantSettings).where(TenantSettings.tenant_id == user.tenant_id)
        )
        ts = ts_result.scalar_one_or_none()
        if ts and ts.default_llm_provider:
            resolved_provider = ts.default_llm_provider
            resolved_model = ts.default_llm_model

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
                messages=history_messages,
                tenant_name=tenant_name,
                context=context_str,
                memory_context=memory_context,
                use_premium=body.use_premium_llm,
                provider=resolved_provider,
                model=resolved_model,
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
        retry_messages = history_messages + [
            {"role": "user", "content": body.content},
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
            use_premium=body.use_premium_llm,
            provider=resolved_provider,
            model=resolved_model,
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
        source_dicts.append(
            {
                "case_name": chunk.get("case_name") or "Unknown Case",
                "citation": chunk.get("citation") or "",
                "court": chunk.get("court"),
                "excerpt": (chunk.get("content") or "")[:300],
            }
        )

    # Track matter context usage if provided
    if hasattr(body, "matter_id") and body.matter_id:
        context_used.insert(0, f"matter:{body.matter_id}")
        context_scores[f"matter:{body.matter_id}"] = 1.0

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
    model_used = settings.PREMIUM_LLM if body.use_premium_llm else settings.PRIMARY_LLM
    skill_applied = (
        body.skill if hasattr(body, "skill") and body.skill else user.default_skill
    )
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
    cost = calculate_cost(
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        model=model_used,
        billing_tier=user.tenant.billing_tier if user.tenant else "payg",
    )
    usage = UsageRecord(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        user_id=user.id,
        conversation_id=conv.id,
        model_used=model_used,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost,
        operation_type="chat",
        query_text=body.content[:2000] if body.content else None,
        rag_chunks_retrieved=len(chunks),
        rag_source_ids=[c["id"] for c in chunks if c.get("id")],
        ip_address=request.client.host if request.client else None,
        user_agent=(request.headers.get("user-agent") or "")[:500] or None,
        cache_hit_rag=cache_hit_rag,
        cache_hit_llm=cache_hit_llm,
        cache_hit_matter=cache_hit_matter,
    )
    db.add(usage)

    await db.commit()
    await db.refresh(assistant_msg)

    # Trigger auto-memory generation if message threshold reached
    await _trigger_auto_memory_generation(db, user, str(conv.id))

    return _message_to_response(assistant_msg)


@router.post("/{conversation_id}/messages/stream")
async def stream_message(
    conversation_id: str,
    body: MessageCreate,
    request: Request,
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
    result = await db.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.tenant_id == user.tenant_id,
        )
    )
    conv = result.scalar_one_or_none()

    if conv is None:
        return StreamingResponse(
            _error_stream("Conversation not found"),
            media_type="text/event-stream",
        )

    if conv.user_id != user.id and user.role != "admin":
        return StreamingResponse(
            _error_stream("Access denied"),
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

    # 3a. Load matter context if provided (with caching)
    matter_context_str = ""
    matter_pii_findings = []
    cache_hit_matter = False
    if hasattr(body, "matter_id") and body.matter_id:
        cached_matter = await cache_manager.get_cached_matter_context(
            matter_id=body.matter_id,
            tenant_id=str(user.tenant_id),
        )
        if cached_matter:
            matter_context_str = cached_matter
            cache_hit_matter = True
        else:
            (
                matter_context_str,
                has_pii,
                matter_pii_findings,
            ) = await matter_context_service.get_safe_matter_context(
                db=db,
                matter_id=body.matter_id,
                privacy_mode=user.privacy_mode,
            )
            await cache_manager.set_cached_matter_context(
                matter_id=body.matter_id,
                tenant_id=str(user.tenant_id),
                context=matter_context_str,
                expertise_level=user.expertise_level,
            )

    # 3b. Session attachments — inject file text directly into context (no embeddings)
    attachment_context = ""
    if hasattr(body, "attachment_ids") and body.attachment_ids:
        try:
            from app.utils.text_processing import extract_text as _extract_text

            attachment_parts = []
            for aid in body.attachment_ids:
                doc_result = await db.execute(
                    select(Document).where(
                        Document.id == aid,
                        Document.tenant_id == user.tenant_id,
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
                            "ORDER BY chunk_index LIMIT 100"
                        ),
                        {"doc_id": str(doc.id)},
                    )
                    text = "\n\n".join(row[0] for row in chunk_result.fetchall())
                else:
                    file_path = doc.file_path or (
                        f"{settings.UPLOAD_DIR}/{user.tenant_id}/{doc.id}/{doc.filename}"
                    )
                    text = await asyncio.to_thread(_extract_text, file_path)

                if text:
                    attachment_parts.append(
                        f"[Attachment: {doc.filename or 'Untitled'}]\n{text[:4000]}"
                    )

            if attachment_parts:
                attachment_context = (
                    "--- Attached Files (session-only, not saved to project) ---\n\n"
                    + "\n\n---\n\n".join(attachment_parts)
                )
        except Exception:
            pass  # Non-fatal

    # 4. RAG: embed question, search chunks, build context (with caching)
    cache_hit_rag = False
    cached_rag = await cache_manager.get_cached_rag_results(
        question=body.content,
        tenant_id=str(user.tenant_id),
        user_id=str(user.id),
        skill=body.skill if hasattr(body, "skill") else user.default_skill,
    )

    if cached_rag:
        context_str, chunks = cached_rag
        cache_hit_rag = True
    else:
        try:
            context_str, chunks, _ = await hybrid_rag_query(
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
        )

    # 4a. Combine matter context with RAG context
    if matter_context_str:
        context_str = f"{matter_context_str}\n\n{context_str}"

    if attachment_context:
        context_str = f"{attachment_context}\n\n{context_str}"

    # 4b. Load user memory context for system prompt
    memory_context = await memory_service.get_memory_context_for_injection(
        db=db,
        user_id=user.id,
    )

    # Resolve provider: if "default", use tenant's configured LLM provider/model
    stream_provider = body.provider
    stream_model = None
    if body.provider == "default":
        ts_result = await db.execute(
            select(TenantSettings).where(TenantSettings.tenant_id == user.tenant_id)
        )
        ts = ts_result.scalar_one_or_none()
        if ts and ts.default_llm_provider:
            stream_provider = ts.default_llm_provider
            stream_model = ts.default_llm_model

    # Create the streaming generator
    async def stream_generator():
        try:
            tenant_name = user.tenant.name if user.tenant else "Legal"

            # Stream tokens from the LLM
            accumulated_text = ""
            async for token in llm_service.stream_complete(
                messages=history_messages,
                tenant_name=tenant_name,
                context=context_str,
                memory_context=memory_context,
                use_premium=body.use_premium_llm,
                provider=stream_provider,
                model=stream_model,
            ):
                accumulated_text += token
                yield f"data: {token}\n\n"

            # Apply guardrails to full response
            cleaned_response, needs_retry, response_pii = apply_guardrails(
                accumulated_text, privacy_mode=user.privacy_mode
            )

            if needs_retry:
                # Clear and retry with explicit instruction
                retry_messages = history_messages + [
                    {"role": "user", "content": body.content},
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
                    use_premium=body.use_premium_llm,
                    provider=stream_provider,
                    model=stream_model,
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
                source_dicts.append(
                    {
                        "case_name": chunk.get("case_name") or "Unknown Case",
                        "citation": chunk.get("citation") or "",
                        "court": chunk.get("court"),
                        "excerpt": (chunk.get("content") or "")[:300],
                    }
                )

            # Track matter context usage if provided
            if hasattr(body, "matter_id") and body.matter_id:
                context_used.insert(0, f"matter:{body.matter_id}")
                context_scores[f"matter:{body.matter_id}"] = 1.0

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
            model_used = (
                settings.PREMIUM_LLM if body.use_premium_llm else settings.PRIMARY_LLM
            )
            skill_applied = (
                body.skill
                if hasattr(body, "skill") and body.skill
                else user.default_skill
            )
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
            cost = calculate_cost(
                tokens_in=int(tokens_in),
                tokens_out=int(tokens_out),
                model=model_used,
                billing_tier=user.tenant.billing_tier if user.tenant else "payg",
            )
            usage = UsageRecord(
                id=uuid.uuid4(),
                tenant_id=user.tenant_id,
                user_id=user.id,
                conversation_id=conv.id,
                model_used=model_used,
                tokens_in=int(tokens_in),
                tokens_out=int(tokens_out),
                cost_usd=cost,
                operation_type="chat_stream",
                query_text=body.content[:2000] if body.content else None,
                rag_chunks_retrieved=len(chunks),
                rag_source_ids=[c["id"] for c in chunks if c.get("id")],
                ip_address=request.client.host if request.client else None,
                user_agent=(request.headers.get("user-agent") or "")[:500] or None,
                cache_hit_rag=cache_hit_rag,
                cache_hit_llm=False,  # Streaming bypasses LLM cache
                cache_hit_matter=cache_hit_matter,
            )
            db.add(usage)

            await db.commit()

            # Trigger auto-memory generation
            await _trigger_auto_memory_generation(db, user, str(conv.id))

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
