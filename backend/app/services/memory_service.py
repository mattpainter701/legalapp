"""User memory management and auto-summarization service."""

from typing import Optional, List
from datetime import datetime, timezone
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User, UserMemory
from app.models.conversation import Message
from app.services.llm import LLMService
from app.services.llm_routing import resolve_llm_route


class MemoryService:
    def __init__(self, llm_service: LLMService):
        self.llm = llm_service

    async def create_or_update_memory(
        self,
        db: AsyncSession,
        user_id: str,
        tenant_id: str,
        memory_type: str,
        key: str,
        value: str | dict,
        confidence: float = 0.5,
    ) -> UserMemory:
        """Create or update a user memory entry."""
        # Check if memory already exists
        result = await db.execute(
            select(UserMemory).where(
                UserMemory.user_id == user_id,
                UserMemory.memory_type == memory_type,
                UserMemory.key == key,
            )
        )
        memory = result.scalar_one_or_none()

        if memory:
            memory.value = value
            memory.confidence = confidence
            memory.updated_at = datetime.now(timezone.utc)
        else:
            memory = UserMemory(
                id=uuid.uuid4(),
                user_id=user_id,
                tenant_id=tenant_id,
                memory_type=memory_type,
                key=key,
                value=value,
                confidence=confidence,
            )
            db.add(memory)

        await db.flush()
        return memory

    async def get_memory(
        self,
        db: AsyncSession,
        user_id: str,
        memory_type: str,
        key: Optional[str] = None,
    ) -> List[UserMemory]:
        """Retrieve user memory entries by type and optionally by key."""
        query = select(UserMemory).where(
            UserMemory.user_id == user_id,
            UserMemory.memory_type == memory_type,
        )
        if key:
            query = query.where(UserMemory.key == key)

        result = await db.execute(query)
        return result.scalars().all()

    async def get_memory_context_for_injection(
        self,
        db: AsyncSession,
        user_id: str,
    ) -> str:
        """
        Format user memory summary and top 3 recent interaction patterns for injection into system prompt.
        Returns formatted string or empty string if no memory available.
        """
        # Get the user's memory summary
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()

        if not user or not user.memory_summary:
            return ""

        memory_parts = [f"User Memory Summary:\n{user.memory_summary}"]

        # Get top 3 recent interaction patterns
        memories = await self.get_memory(
            db=db,
            user_id=user_id,
            memory_type="interaction_pattern",
        )

        if memories:
            recent_interactions = memories[-3:] if len(memories) >= 3 else memories
            memory_parts.append("\nRecent Interactions:")
            for i, m in enumerate(recent_interactions, 1):
                interaction_text = str(m.value)[
                    :200
                ]  # Limit to 200 chars per interaction
                memory_parts.append(f"{i}. {interaction_text}")

        return "\n".join(memory_parts)

    async def summarize_conversation(
        self,
        db: AsyncSession,
        user_id: str,
        tenant_id: str,
        conversation_id: str,
        tenant_name: str = "Legal",
    ) -> str:
        """
        Generate a summary of a conversation and extract key facts.
        Returns summary text.
        """
        # Load all messages from conversation
        result = await db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.asc())
        )
        messages = result.scalars().all()

        if not messages:
            return ""

        # Build conversation text
        conversation_text = ""
        for msg in messages:
            role = "User" if msg.role == "user" else "Assistant"
            conversation_text += f"{role}: {msg.content[:500]}\n"

        # Generate summary with LLM
        summary_prompt = f"""Summarize this legal research conversation in 2-3 sentences.
Focus on:
1. Main question or topic
2. Key findings or recommendations
3. Any notable legal citations or concepts

Conversation:
{conversation_text[:2000]}

Summary:"""

        route = await resolve_llm_route(db, tenant_id, use_premium=False)
        summary_text, _, _ = await self.llm.complete(
            messages=[{"role": "user", "content": summary_prompt}],
            tenant_name=tenant_name,
            context="",
            use_premium=False,
            provider=route.provider,
            model=route.model,
        )

        # Store as interaction_pattern memory
        await self.create_or_update_memory(
            db=db,
            user_id=user_id,
            tenant_id=tenant_id,
            memory_type="interaction_pattern",
            key=f"conversation_{conversation_id[:8]}",
            value=summary_text,
            confidence=0.8,
        )

        return summary_text

    async def update_user_memory_summary(
        self,
        db: AsyncSession,
        user_id: str,
        tenant_id: str,
        tensor_name: str = "Legal",
    ) -> str:
        """
        Generate overall memory summary from recent interaction patterns.
        Updates User.memory_summary field.
        """
        # Get recent interaction patterns
        memories = await self.get_memory(
            db=db,
            user_id=user_id,
            memory_type="interaction_pattern",
        )

        if not memories:
            memory_text = "No interactions recorded."
        else:
            memory_text = "\n".join(
                [f"- {m.key}: {str(m.value)[:200]}" for m in memories[-5:]]  # Last 5
            )

        # Get preferences
        preferences = await self.get_memory(
            db=db,
            user_id=user_id,
            memory_type="preference",
        )

        if preferences:
            memory_text += "\n\nPreferences:\n"
            memory_text += "\n".join([f"- {p.key}: {p.value}" for p in preferences])

        # Update user record
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()

        if user:
            user.memory_summary = memory_text
            user.last_memory_update = datetime.now(timezone.utc)
            await db.flush()

        return memory_text
