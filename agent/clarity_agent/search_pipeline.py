"""Extension points intentionally left for later Search Node phases."""

from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator, Protocol

from clarity_agent.search_engine import DocumentChunk


@dataclass(frozen=True)
class CrawlItem:
    document_id: str
    share_id: str
    source_path: str
    relative_path: str
    content_hash: str
    size_bytes: int


class Crawler(Protocol):
    """Discovers changed documents without extracting their body."""

    def crawl(self) -> AsyncIterator[CrawlItem]: ...


class Extractor(Protocol):
    """Produces page/section-aware chunks on customer infrastructure."""

    def extract(self, item: CrawlItem) -> AsyncIterator[DocumentChunk]: ...


class AclFilter(Protocol):
    """Resolves stable local principal tokens used for query-time trimming."""

    async def tokens_for_document(self, item: CrawlItem) -> tuple[str, ...]: ...

    async def tokens_for_request(self, principal: str) -> tuple[str, ...]: ...
