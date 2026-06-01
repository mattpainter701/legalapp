import uuid
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.models.conversation import Conversation, Message, UsageRecord
from app.schemas.chat import (
    ConversationCreate,
    ConversationResponse,
    ConversationDetail,
    MessageCreate,
    MessageResponse,
    SourceCitation,
)
from app.services.embeddings import EmbeddingService
from app.services.rag import full_rag_query
from app.services.llm import LLMService
from app.services.billing import calculate_cost
from app.utils.guardrails import apply_guardrails

settings = get_settings()
router = APIRouter(prefix="/conversations", tags=["chat"])

embedding_service = EmbeddingService()
llm_service = LLMService()


def _conversation_to_response(conv: Conversation, message_count: int = None) -> ConversationResponse:
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
        _conversation_to_response(c, count_map.get(str(c.id), 0))
        for c in conversations
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


@router.post("/{conversation_id}/messages", response_model=MessageResponse, status_code=201)
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

    # 2. Save user message
    user_msg = Message(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        conversation_id=conv.id,
        role="user",
        content=body.content,
        sources=None,
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

    # 4. RAG: embed question, search chunks, build context
    context_str, chunks = await full_rag_query(
        db=db,
        embedding_service=embedding_service,
        question=body.content,
        tenant_id=str(user.tenant_id),
        include_public=body.include_public,
    )

    # 5. Call LLM
    tenant_name = user.tenant.name if user.tenant else "Legal"
    response_text, tokens_in, tokens_out = await llm_service.complete(
        messages=history_messages,
        tenant_name=tenant_name,
        context=context_str,
        use_premium=body.use_premium_llm,
    )

    # 6. Apply guardrails
    cleaned_response, needs_retry = apply_guardrails(response_text)

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
            use_premium=body.use_premium_llm,
        )
        cleaned_response, _ = apply_guardrails(response_text2)
        tokens_in += tokens_in2
        tokens_out += tokens_out2

    # Build source citations from retrieved chunks
    source_dicts = []
    seen_citations: set = set()
    for chunk in chunks:
        citation_key = chunk.get("citation") or chunk.get("case_name") or ""
        if citation_key and citation_key in seen_citations:
            continue
        if citation_key:
            seen_citations.add(citation_key)
        source_dicts.append(
            {
                "case_name": chunk.get("case_name") or "Unknown Case",
                "citation": chunk.get("citation") or "",
                "court": chunk.get("court"),
                "excerpt": (chunk.get("content") or "")[:300],
            }
        )

    # 7. Save assistant message
    model_used = settings.PREMIUM_LLM if body.use_premium_llm else settings.PRIMARY_LLM
    assistant_msg = Message(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        conversation_id=conv.id,
        role="assistant",
        content=cleaned_response,
        sources=source_dicts if source_dicts else None,
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
    )
    db.add(usage)

    await db.commit()
    await db.refresh(assistant_msg)

    return _message_to_response(assistant_msg)
