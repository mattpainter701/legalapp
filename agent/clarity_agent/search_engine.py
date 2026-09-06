"""Local full-text engine contracts for the production Search Node.

The contracts deliberately do not mention the SaaS transport. Implementations
receive extracted, ACL-labelled chunks and return bounded local results. Raw
document text must never be placed in control-state stores or relay metadata.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import AsyncIterator, Sequence

# 2 adds deny_acl_tokens. Nothing indexes through this engine yet, so the
# mapping change costs no customer rebuild today; it would once a crawler does.
INDEX_SCHEMA_VERSION = 2


@dataclass(frozen=True)
class DocumentChunk:
    document_id: str
    chunk_id: str
    share_id: str
    relative_path: str
    filename: str
    extension: str
    content: str
    content_hash: str
    modified_at: datetime
    mutation_generation: int
    document_version: str = ""
    page_number: int | None = None
    section_path: tuple[str, ...] = ()
    ordinal: int = 0
    start_offset: int | None = None
    end_offset: int | None = None
    matter_ids: tuple[str, ...] = ()
    # Principals allowed to read this document. An empty set is unreachable:
    # the query requires a match, so a document with no allow tokens is never
    # returned to anyone.
    acl_tokens: tuple[str, ...] = ()
    # Principals explicitly denied. Windows resolves an explicit DENY ACE ahead
    # of any allow, including one inherited through a group, so an allow-only
    # index cannot express the access model and would over-return.
    deny_acl_tokens: tuple[str, ...] = ()
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SearchFilters:
    share_ids: tuple[str, ...] = ()
    matter_ids: tuple[str, ...] = ()
    extensions: tuple[str, ...] = ()
    document_ids: tuple[str, ...] = ()
    modified_after: datetime | None = None
    modified_before: datetime | None = None
    path_scopes: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class SearchRequest:
    query: str
    acl_tokens: tuple[str, ...]
    filters: SearchFilters = field(default_factory=SearchFilters)
    limit: int = 20
    offset: int = 0
    highlight: bool = True
    timeout_ms: int = 2_000


@dataclass(frozen=True)
class SearchHit:
    document_id: str
    chunk_id: str
    share_id: str
    relative_path: str
    filename: str
    extension: str
    score: float
    snippet: str
    page_number: int | None
    section_path: tuple[str, ...]
    ordinal: int
    document_version: str = ""


@dataclass(frozen=True)
class SearchResponse:
    hits: tuple[SearchHit, ...]
    total: int
    took_ms: int
    timed_out: bool
    engine: str
    index_schema_version: int


@dataclass(frozen=True)
class BulkResult:
    accepted: int
    failed_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class DocumentMutation:
    document_id: str
    generation: int


@dataclass(frozen=True)
class EngineHealth:
    status: str
    engine: str
    index_schema_version: int
    active_index: str | None
    details: dict[str, object] = field(default_factory=dict)


class LocalSearchEngine(ABC):
    """Production serving-engine boundary."""

    @abstractmethod
    async def ensure_index(self) -> str: ...

    @abstractmethod
    async def bulk_index(self, chunks: Sequence[DocumentChunk]) -> BulkResult: ...

    @abstractmethod
    async def delete_documents(self, mutations: Sequence[DocumentMutation]) -> int: ...

    @abstractmethod
    async def search(self, request: SearchRequest) -> SearchResponse: ...

    @abstractmethod
    async def rebuild(self, chunks: AsyncIterator[DocumentChunk]) -> str: ...

    @abstractmethod
    async def health(self) -> EngineHealth: ...

    @abstractmethod
    async def create_snapshot(self, repository: str, snapshot: str) -> dict: ...

    @abstractmethod
    async def restore_snapshot(self, repository: str, snapshot: str) -> dict: ...

    @abstractmethod
    async def close(self) -> None: ...
