"""Pydantic schemas for SMB file share relay agent operations."""

from datetime import datetime

from pydantic import BaseModel, Field


# ── Agent schemas ───────────────────────────────────────────────────────────


class PairingCodeRequest(BaseModel):
    pass


class PairingCodeResponse(BaseModel):
    pairing_code: str
    expires_at: datetime


class AgentRegisterRequest(BaseModel):
    pairing_code: str
    agent_name: str = Field(..., max_length=200)
    agent_version: str | None = Field(None, max_length=50)
    hostname: str | None = Field(None, max_length=200)
    os_info: str | None = Field(None, max_length=200)


class AgentRegisterResponse(BaseModel):
    agent_id: str
    api_key: str


class AgentHeartbeatRequest(BaseModel):
    agent_version: str | None = Field(None, max_length=50)
    hostname: str | None = Field(None, max_length=200)
    active_scans: int | None = None


class AgentInfo(BaseModel):
    id: str
    agent_name: str
    status: str
    agent_version: str | None
    hostname: str | None
    os_info: str | None
    last_heartbeat: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class AgentStatusUpdate(BaseModel):
    status: str = Field(..., max_length=20)


# ── Share schemas ───────────────────────────────────────────────────────────


class ShareCreate(BaseModel):
    share_path: str = Field(..., max_length=500)
    display_name: str | None = Field(None, max_length=200)
    file_extensions: list[str] | None = None
    max_depth: int | None = Field(None, ge=0, le=50)
    scan_schedule: str | None = Field(None, max_length=50)


class ShareUpdate(BaseModel):
    display_name: str | None = Field(None, max_length=200)
    file_extensions: list[str] | None = None
    max_depth: int | None = Field(None, ge=0, le=50)
    scan_schedule: str | None = Field(None, max_length=50)


class ShareInfo(BaseModel):
    id: str
    agent_id: str
    share_path: str
    display_name: str | None
    file_extensions: list[str] | None
    max_depth: int
    scan_schedule: str
    last_scan_at: datetime | None
    last_scan_status: str | None
    last_scan_file_count: int | None
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Sync schemas ────────────────────────────────────────────────────────────


class FileSyncEntry(BaseModel):
    path: str
    filename: str = Field(..., max_length=500)
    ext: str | None = Field(None, max_length=20)
    mime_type: str | None = Field(None, max_length=200)
    snippet: str | None = None
    owner: str | None = Field(None, max_length=300)
    size_bytes: int | None = None
    modified_time: datetime | None = None
    created_time: datetime | None = None


class SyncRequest(BaseModel):
    files: list[FileSyncEntry]
    deletions: list[str] = []


class SyncResponse(BaseModel):
    synced: int
    deleted: int
    errors: list[dict] = []


# ── Task schemas ────────────────────────────────────────────────────────────


class ContentFetchTask(BaseModel):
    task_id: str
    file_path: str
    reason: str = "search_result"


class ContentFetchResult(BaseModel):
    task_id: str
    content: str
    truncated: bool = False
    error: str | None = None


# ── Search schemas ──────────────────────────────────────────────────────────


class SmbSearchRequest(BaseModel):
    q: str = Field(..., min_length=1)
    matter_id: str | None = None
    file_extensions: list[str] | None = None
    limit: int = Field(20, ge=1, le=100)


class SmbSearchResult(BaseModel):
    id: str
    path: str
    filename: str
    ext: str | None
    snippet: str | None
    owner: str | None
    size_bytes: int | None
    modified_time: datetime | None
    created_time: datetime | None
    score: float | None

    model_config = {"from_attributes": True}


# ── Matter binding schemas ────────────────────────────────────────────────


class MatterSmbShareCreate(BaseModel):
    share_id: str
    folder_path: str | None = Field(None, max_length=500)
    display_label: str | None = Field(None, max_length=200)
    auto_scan: bool = True


class MatterSmbShareInfo(BaseModel):
    id: str
    matter_id: str
    share_id: str
    folder_path: str | None
    display_label: str | None
    auto_scan: bool
    share_path: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Access log schemas ─────────────────────────────────────────────────────


class SmbAccessLogEntry(BaseModel):
    id: str
    user_id: str | None
    file_path: str
    access_reason: str | None
    bytes_sent: int | None
    accessed_at: datetime

    model_config = {"from_attributes": True}
