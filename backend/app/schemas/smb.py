"""Pydantic schemas for SMB file share relay agent operations."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


def canonical_unc_path(value: str) -> str:
    """Normalize and validate a share UNC path for storage and matching."""
    normalized = (value or "").replace("/", "\\").strip()
    if not normalized.startswith("\\\\"):
        raise ValueError("Share path must be a UNC path like \\\\server\\share")
    parts = [part for part in normalized[2:].split("\\") if part not in ("", ".")]
    if len(parts) < 2:
        raise ValueError("Share path must include a server and share")
    if any(part == ".." for part in parts):
        raise ValueError(
            "Share path must include a server and share and cannot contain .."
        )
    if any(
        part.endswith((" ", "."))
        or any(ord(char) < 32 or char in '<>:"|?*' for char in part)
        for part in parts
    ):
        raise ValueError("Share path contains characters Windows does not support")
    return "\\\\" + "\\".join(parts)


class UuidStringModel(BaseModel):
    """Base for response models that expose database UUIDs as strings.

    ``model_validate`` on an ORM row hands these fields real ``uuid.UUID``
    objects, which a plain ``str`` annotation rejects, so coerce them here
    rather than repeating ``str(...)`` at every call site.
    """

    @field_validator("*", mode="before")
    @classmethod
    def _uuid_to_str(cls, value):
        return str(value) if isinstance(value, uuid.UUID) else value


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
    update_status: str | None = Field(None, pattern="^(in_progress|completed|failed)$")
    update_target_version: str | None = Field(
        None, pattern=r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$"
    )
    update_error: str | None = Field(None, max_length=2000)


class AgentInfo(UuidStringModel):
    id: str
    agent_name: str
    status: str
    agent_version: str | None
    hostname: str | None
    os_info: str | None
    last_heartbeat: datetime | None
    created_at: datetime
    updated_at: datetime | None = None
    is_registered: bool = True
    update_status: str = "idle"
    update_target_version: str | None = None
    update_manifest_id: str | None = None
    update_task_id: str | None = None
    update_requested_at: datetime | None = None
    update_completed_at: datetime | None = None
    update_error: str | None = None

    model_config = {"from_attributes": True}


class AgentStatusUpdate(BaseModel):
    status: str = Field(..., max_length=20)


# ── Share schemas ───────────────────────────────────────────────────────────


class ShareCreate(BaseModel):
    share_path: str = Field(..., max_length=500)
    display_name: str | None = Field(None, max_length=200)
    file_extensions: list[str] | None = None
    exclude_patterns: list[str] | None = None
    max_depth: int | None = Field(None, ge=0, le=50)
    scan_schedule: str | None = Field(None, max_length=50)
    is_enabled: bool | None = None
    # Existing stored credential to mount this share with. When omitted, the
    # agent falls back to the identity it runs as.
    credential_id: str | None = None
    # Alternatively, create-and-attach a credential in the same request so the
    # admin never has to leave the "add share" flow.
    credential: "SmbCredentialCreate | None" = None

    @field_validator("share_path")
    @classmethod
    def _canonical_share_path(cls, value):
        return canonical_unc_path(value)


class ShareUpdate(BaseModel):
    share_path: str | None = Field(None, max_length=500)
    agent_id: str | None = None
    display_name: str | None = Field(None, max_length=200)
    file_extensions: list[str] | None = None
    exclude_patterns: list[str] | None = None
    max_depth: int | None = Field(None, ge=0, le=50)
    scan_schedule: str | None = Field(None, max_length=50)
    is_enabled: bool | None = None
    # Empty string detaches the credential; None leaves it unchanged.
    credential_id: str | None = None
    credential: "SmbCredentialCreate | None" = None

    @field_validator("share_path")
    @classmethod
    def _canonical_share_path(cls, value):
        return canonical_unc_path(value) if value is not None else value


class ShareInfo(UuidStringModel):
    id: str
    agent_id: str
    share_path: str
    display_name: str | None
    file_extensions: list[str] | None
    exclude_patterns: list[str] | None = None
    max_depth: int
    scan_schedule: str
    is_enabled: bool = True
    credential_id: str | None = None
    credential_name: str | None = None
    agent_name: str | None = None
    last_scan_at: datetime | None
    last_scan_status: str | None
    last_scan_file_count: int | None
    last_scan_error: str | None = None
    last_verified_at: datetime | None = None
    last_verify_status: str | None = None
    last_verify_error: str | None = None
    created_at: datetime
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


# ── Credential schemas ──────────────────────────────────────────────────────


class SmbCredentialCreate(BaseModel):
    name: str = Field(..., max_length=200)
    auth_method: str = Field("ntlm", max_length=20)
    domain: str | None = Field(None, max_length=200)
    username: str | None = Field(None, max_length=200)
    # Write-only: the secret is encrypted at rest and never read back.
    password: str | None = Field(None, max_length=1024)
    # Optionally pin the credential to a single agent.
    agent_id: str | None = None


class SmbCredentialUpdate(BaseModel):
    name: str | None = Field(None, max_length=200)
    auth_method: str | None = Field(None, max_length=20)
    domain: str | None = Field(None, max_length=200)
    username: str | None = Field(None, max_length=200)
    password: str | None = Field(None, max_length=1024)
    agent_id: str | None = None
    is_active: bool | None = None


class SmbCredentialInfo(UuidStringModel):
    """Admin-facing view of a credential. Deliberately has no password field."""

    id: str
    name: str
    auth_method: str
    domain: str | None
    username: str | None
    has_password: bool = False
    agent_id: str | None = None
    is_active: bool = True
    share_count: int = 0
    last_verified_at: datetime | None = None
    last_verify_status: str | None = None
    last_verify_error: str | None = None
    last_delivered_at: datetime | None = None
    created_at: datetime
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


# ── Agent-facing share schemas ──────────────────────────────────────────────


class AgentShareCredential(UuidStringModel):
    """Plaintext credential handed to an authenticated agent over TLS."""

    credential_id: str
    name: str
    auth_method: str
    domain: str | None = None
    username: str | None = None
    password: str | None = None


class AgentShareInfo(UuidStringModel):
    """Share config as the agent needs it, including the mount credential."""

    share_id: str
    share_path: str
    server: str | None = None
    share: str | None = None
    root_path: str | None = None
    display_name: str | None = None
    file_extensions: list[str] | None = None
    exclude_patterns: list[str] | None = None
    max_depth: int = 10
    scan_schedule: str = "0 */6 * * *"
    is_enabled: bool = True
    credential: AgentShareCredential | None = None


class ShareScanStatus(BaseModel):
    """Scan outcome reported by the agent after it finishes a share."""

    status: str = Field(..., max_length=20)
    file_count: int | None = Field(None, ge=0)
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


# ── Sync schemas ────────────────────────────────────────────────────────────


class FileSyncEntry(BaseModel):
    path: str = Field(..., min_length=5, max_length=32768)
    filename: str = Field(..., max_length=500)
    ext: str | None = Field(None, max_length=20)
    mime_type: str | None = Field(None, max_length=200)
    snippet: str | None = Field(None, max_length=10000)
    owner: str | None = Field(None, max_length=300)
    size_bytes: int | None = None
    modified_time: datetime | None = None
    created_time: datetime | None = None

    @field_validator("modified_time", "created_time", mode="before")
    @classmethod
    def _empty_timestamp_is_null(cls, value):
        """Accept older agents' empty-string timestamp sentinel."""
        return None if value == "" else value


class SyncRequest(BaseModel):
    files: list[FileSyncEntry] = Field(default_factory=list, max_length=500)
    deletions: list[str] = Field(default_factory=list, max_length=500)


class SyncResponse(BaseModel):
    synced: int
    deleted: int
    errors: list[dict] = Field(default_factory=list)


# ── Task schemas ────────────────────────────────────────────────────────────


class ContentFetchTask(BaseModel):
    """A unit of work an agent polls for.

    ``kind`` distinguishes an on-demand content fetch from the operational
    tasks the admin console triggers (connection test, scan now). Older agents
    that ignore the field still handle content fetches correctly.
    """

    task_id: str
    kind: str = "content_fetch"
    file_path: str = ""
    share_id: str | None = None
    share_path: str | None = None
    reason: str = "search_result"
    target_version: str | None = Field(
        None, pattern=r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$"
    )
    manifest_id: str | None = Field(
        None, pattern=r"^agent-v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$"
    )
    # Local full-text search fields.  They are optional so older task kinds
    # and agents continue to parse the existing content-fetch contract.
    query: str | None = Field(None, min_length=1, max_length=1000)
    scopes: list[dict] | None = Field(None, max_length=100)
    file_extensions: list[str] | None = Field(None, max_length=50)
    limit: int | None = Field(None, ge=1, le=50)
    correlation_id: str | None = Field(
        None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
    )


class ContentFetchResult(BaseModel):
    task_id: str = Field(..., min_length=1, max_length=128)
    # The shipped agent returns at most 512 KiB of extracted text. Leave room
    # for Unicode/format variance while bounding an authenticated bad device.
    content: str = Field("", max_length=1_000_000)
    truncated: bool = False
    error: str | None = Field(None, max_length=2000)
    # Set by verify/scan tasks; content fetches leave it at the default.
    ok: bool = True
    detail: dict | None = None


class LocalSearchResultHit(BaseModel):
    """Strict untrusted result row accepted from a local-search worker."""

    model_config = {"extra": "forbid", "allow_inf_nan": False}

    share_id: str = Field(..., min_length=1, max_length=128)
    relative_path: str = Field(..., min_length=1, max_length=32768)
    filename: str = Field(..., min_length=1, max_length=500)
    ext: str | None = Field(None, max_length=20)
    snippet: str | None = Field(None, max_length=10000)
    page_number: int | None = Field(None, ge=1, le=10_000_000)
    score: float = Field(0.0, ge=-1_000_000, le=1_000_000)


class LocalSearchResultDetail(BaseModel):
    """Versioned, bounded result envelope for the local-search task kind."""

    model_config = {"extra": "forbid"}

    schema_version: Literal[1]
    correlation_id: str = Field(..., pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    hits: list[LocalSearchResultHit] = Field(default_factory=list, max_length=50)
    index_state: str = Field(..., min_length=1, max_length=40)
    indexed_files: int = Field(0, ge=0, le=1_000_000_000)
    pending_files: int = Field(0, ge=0, le=1_000_000_000)
    duration_ms: int = Field(0, ge=0, le=86_400_000)


class TaskAck(BaseModel):
    """Response for an admin-triggered agent task."""

    task_id: str
    agent_id: str
    kind: str


# ── Search schemas ──────────────────────────────────────────────────────────


class SmbSearchRequest(BaseModel):
    q: str = Field(..., min_length=1)
    matter_id: str | None = None
    file_extensions: list[str] | None = None
    limit: int = Field(20, ge=1, le=100)


class FirmMemorySearchRequest(BaseModel):
    """Matter-scoped search executed by the customer's local agent."""

    query: str = Field(..., min_length=1, max_length=1000)
    matter_id: str = Field(..., min_length=1, max_length=64)
    file_extensions: list[str] | None = Field(None, max_length=50)
    limit: int = Field(20, ge=1, le=50)
    correlation_id: str | None = Field(
        None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
    )

    @field_validator("query")
    @classmethod
    def _trim_query(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("query must not be blank")
        return value

    @field_validator("file_extensions")
    @classmethod
    def _normalize_extensions(cls, value):
        if value is None:
            return value
        normalized = []
        for ext in value:
            ext = ext.strip().lower()
            if ext and not ext.startswith("."):
                ext = "." + ext
            if ext and len(ext) <= 20 and ext not in normalized:
                normalized.append(ext)
        return normalized or None


class FirmMemorySearchHit(UuidStringModel):
    id: str
    path: str
    filename: str
    ext: str | None
    snippet: str | None
    page_number: int | None = None
    score: float | None
    owner: str | None
    size_bytes: int | None
    modified_time: datetime | None
    created_time: datetime | None
    share_id: str


class FirmMemoryAgentStatus(BaseModel):
    agent_id: str
    status: str
    index_state: str | None = None
    indexed_files: int = 0
    pending_files: int = 0
    duration_ms: int | None = None


class FirmMemorySearchResponse(BaseModel):
    correlation_id: str
    hits: list[FirmMemorySearchHit] = Field(default_factory=list)
    duration_ms: int
    agent_statuses: list[FirmMemoryAgentStatus] = Field(default_factory=list)
    partial: bool = False
    degraded: bool = False
    errors: list[str] = Field(default_factory=list, max_length=20)


class SmbSearchResult(UuidStringModel):
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


class MatterSmbShareInfo(UuidStringModel):
    id: str
    matter_id: str
    share_id: str
    folder_path: str | None
    display_label: str | None
    auto_scan: bool
    share_path: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Access log schemas ─────────────────────────────────────────────────────


class SmbAccessLogEntry(UuidStringModel):
    id: str
    user_id: str | None
    file_path: str
    access_reason: str | None
    bytes_sent: int | None
    accessed_at: datetime

    model_config = {"from_attributes": True}


# ``ShareCreate``/``ShareUpdate`` reference ``SmbCredentialCreate``, which is
# declared after them, so resolve those forward references explicitly.
ShareCreate.model_rebuild()
ShareUpdate.model_rebuild()
