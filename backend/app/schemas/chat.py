from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field, field_validator


class ConversationCreate(BaseModel):
    title: Optional[str] = None
    matter_id: Optional[str] = None


class ConversationUpdate(BaseModel):
    title: Optional[str] = None
    matter_id: Optional[str] = None


class ConversationResponse(BaseModel):
    id: str
    title: str
    matter_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    message_count: Optional[int] = None
    attachment_count: int = 0

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
    source_id: Optional[str] = None
    case_name: str
    citation: str
    court: Optional[str] = None
    excerpt: str
    url: Optional[str] = None
    source_type: Optional[str] = None
    source_label: Optional[str] = None
    locator: Optional[str] = None
    retrieval_jurisdiction: Optional[str] = None
    relevance_score: Optional[float] = None
    authority_tier: Optional[str] = None
    official_status: Optional[str] = None
    effective_date: Optional[str] = None
    cited: bool = False


class CitationMarker(BaseModel):
    source_id: str
    start: int
    end: int


class CitationTagSpan(BaseModel):
    start: int
    end: int


class CitationAnnotation(BaseModel):
    claim_id: str
    start: int
    end: int
    text: str
    support: str
    source_ids: List[str] = Field(default_factory=list)
    source_markers: List[CitationMarker] = Field(default_factory=list)
    support_tag: CitationTagSpan


class ArtifactSummary(BaseModel):
    """Metadata about a document artifact attached to a message."""

    id: str
    title: str
    format: str = "markdown"
    version: int = 1
    saved_to_matter: bool = False

    model_config = {"from_attributes": True}


class MessageResponse(BaseModel):
    id: str
    conversation_id: str
    role: str
    content: str
    sources: List[SourceCitation] = []
    citation_annotations: List[CitationAnnotation] = Field(default_factory=list)
    # Reviewable work the assistant proposed on this turn. Empty for every
    # tenant without chat actions enabled, which is the default.
    proposed_actions: List[dict] = []
    artifacts: List[ArtifactSummary] = []
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

    @field_validator("id", mode="before")
    @classmethod
    def serialize_uuid_id(cls, value):
        if value is None:
            return value
        return str(value)

    model_config = {"from_attributes": True}
