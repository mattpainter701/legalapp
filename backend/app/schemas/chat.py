from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel


class ConversationCreate(BaseModel):
    title: Optional[str] = None


class ConversationResponse(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: Optional[int] = None

    model_config = {"from_attributes": True}


class MessageCreate(BaseModel):
    content: str
    include_public: bool = True
    use_premium_llm: bool = False
    provider: str = "default"  # "default" | "openrouter" | "gemini" | "azure"
    skill: Optional[str] = None  # e.g., "commercial-legal", "litigation-legal"
    matter_id: Optional[str] = None  # UUID of related matter for context injection


class SourceCitation(BaseModel):
    case_name: str
    citation: str
    court: Optional[str] = None
    excerpt: str


class MessageResponse(BaseModel):
    id: str
    conversation_id: str
    role: str
    content: str
    sources: List[SourceCitation] = []
    created_at: datetime

    model_config = {"from_attributes": True}


class ConversationDetail(BaseModel):
    conversation: ConversationResponse
    messages: List[MessageResponse]
