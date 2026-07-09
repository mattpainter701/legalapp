from datetime import datetime
from typing import List
from pydantic import BaseModel


class DocumentResponse(BaseModel):
    id: str
    filename: str
    content_type: str | None = None
    file_size: int | None = None
    status: str
    chunk_count: int
    created_at: datetime
    processing_job_id: str | None = None

    model_config = {"from_attributes": True}


class DocumentList(BaseModel):
    documents: List[DocumentResponse]
    total: int
