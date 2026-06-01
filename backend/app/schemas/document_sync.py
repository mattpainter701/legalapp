from pydantic import BaseModel


class DocumentSyncRequest(BaseModel):
    provider: str = "onedrive"
    user_id: str | None = None
    max_files: int = 50
    download_and_index: bool = False
    site_id: str | None = None


class SyncedDocument(BaseModel):
    id: str
    name: str
    size: int
    modified: str | None = None
    url: str | None = None
    drive: str
    mime_type: str | None = None


class DocumentSyncResponse(BaseModel):
    provider: str
    documents_found: int
    documents_downloaded: int = 0
    documents: list[SyncedDocument]


class DocumentSyncStats(BaseModel):
    onedrive: int = 0
    sharepoint: int = 0
    google_drive: int = 0
