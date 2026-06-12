from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel


class ConversationCreate(BaseModel):
    title: Optional[str] = None
    matter_id: Optional[str] = None


class ConversationResponse(BaseModel):
    id: str
    title: str
    matter_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    message_count: Optional[int] = None

    model_config = {"from_attributes": True}


class MessageCreate(BaseModel):
    content: str
    include_public: bool = True
    use_premium_llm: bool = False
    provider: str = "default"  # compatibility route: "default" | "standard" | "premium"
    skill: Optional[str] = None  # e.g., "commercial-legal", "litigation-legal"
    matter_id: Optional[str] = None  # UUID of related matter for context injection
    attachment_ids: list[
        str
    ] = []  # UUIDs of uploaded documents to inject inline (no embeddings needed)


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


class ChatAttachmentResponse(BaseModel):
    id: str
    filename: str
    content_type: Optional[str] = None
    file_size: Optional[int] = None
    expires_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}
