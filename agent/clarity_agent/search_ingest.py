"""The seam between FM-05 extraction records and the FM-03 serving engine.

FM-03 (`opensearch_engine`), FM-04 (`crawl_control`) and FM-05 (`search-node`)
each landed with their own vocabulary and no adapter joined them, so the
OpenSearch path indexes nothing. This module is that adapter's translation half:
it turns one extraction record into the document envelope the engine accepts.

It deliberately does not import `search_node`. That package is a separate
distribution which must not run in the agent process, and it states that it
contains no OpenSearch client. The record is therefore consumed structurally —
by the attributes `search_node.contracts.ExtractionRecord` defines — so the
worker and the engine stay in separate processes with the transport between
them chosen later.

## Two fields the FM-05 contract does not carry

`ExtractionRecord` has no `modified_at` and no `mutation_generation`, but the
engine requires both: `modified_at` backs the date filter, and the generation is
what fences a delayed worker's write against a newer one. They are therefore
explicit arguments here, and whatever wires the queue must supply them from the
crawl manifest's `FileStat`/`LeasedJob`, which is where both actually live.
Inventing either at this boundary would silently break generation fencing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol, Sequence, runtime_checkable

from clarity_agent.search_engine import DocumentChunk

INDEXABLE_STATUS = "indexed-ready"


@runtime_checkable
class ExtractionSection(Protocol):
    """The shape of `search_node.contracts.Section`."""

    ordinal: int
    text: str
    page_number: int | None
    chunk_id: str
    section_path: tuple[str, ...]
    start_offset: int | None
    end_offset: int | None


@runtime_checkable
class ExtractionOutcome(Protocol):
    """The shape of `search_node.contracts.ExtractionRecord`."""

    document_id: str
    share_id: str
    relative_path: str
    filename: str
    extension: str
    content_version: str
    content_fingerprint: str
    status: object
    sections: Sequence[ExtractionSection]
    matter_ids: tuple[str, ...]
    acl_tokens: tuple[str, ...]
    acl_state: str


class AclNotCaptured(ValueError):
    """The record's ACLs are not proven, so it must not become searchable."""


def is_indexable(record: ExtractionOutcome) -> bool:
    """Only a terminal success carries text worth indexing.

    Every other terminal status is a classified failure the manifest keeps
    visible; publishing one would present an empty document as a searchable one.
    """
    return str(getattr(record.status, "value", record.status)) == INDEXABLE_STATUS


def document_chunks(
    record: ExtractionOutcome,
    *,
    modified_at: datetime,
    mutation_generation: int,
    deny_acl_tokens: Sequence[str] = (),
) -> tuple[DocumentChunk, ...]:
    """Translate one extraction record into one atomic document envelope.

    Fails closed on ACL state: a record whose ACLs were never captured has no
    allow set, and the engine requires an allow match, so indexing it would
    quietly produce a document nobody can retrieve — or, worse, invite a future
    caller to treat the empty set as "unrestricted".
    """
    if not is_indexable(record):
        raise ValueError("only an indexed-ready record produces document chunks")
    if mutation_generation < 1:
        raise ValueError("document mutation generation must be positive")
    if record.acl_state != "healthy" or not record.acl_tokens:
        raise AclNotCaptured("refusing to index a document with no proven allow set")
    if modified_at.tzinfo is None:
        raise ValueError("modified_at must be timezone-aware")
    sections = [section for section in record.sections if section.text]
    if not sections:
        raise ValueError("an indexed-ready record must carry at least one section")

    normalized = modified_at.astimezone(timezone.utc)
    allow = tuple(record.acl_tokens)
    deny = tuple(deny_acl_tokens)
    # Every chunk of a document must agree on metadata and generation; the
    # engine rejects the envelope otherwise.
    return tuple(
        DocumentChunk(
            document_id=record.document_id,
            chunk_id=section.chunk_id or f"{record.document_id}:{section.ordinal}",
            share_id=record.share_id,
            relative_path=record.relative_path,
            filename=record.filename,
            extension=record.extension,
            content=section.text,
            content_hash=record.content_fingerprint,
            modified_at=normalized,
            mutation_generation=mutation_generation,
            # The engine treats document_version as the identity of the content
            # it indexed. content_version is exactly that on the FM-05 side.
            document_version=record.content_version,
            page_number=section.page_number,
            section_path=tuple(section.section_path or ()),
            ordinal=section.ordinal,
            start_offset=section.start_offset,
            end_offset=section.end_offset,
            matter_ids=tuple(record.matter_ids),
            acl_tokens=allow,
            deny_acl_tokens=deny,
        )
        for section in sections
    )
