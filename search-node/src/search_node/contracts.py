"""Engine-neutral queue and search-sink contracts.

These records deliberately contain no OpenSearch implementation and no crawler
reconciliation logic. Queue and sink adapters can live in later, independent
changes without importing an untrusted parser into either API process.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
import hashlib
from typing import Any, Protocol


class TerminalStatus(StrEnum):
    INDEXED_READY = "indexed-ready"
    UNSUPPORTED = "unsupported"
    ENCRYPTED = "encrypted"
    CORRUPT = "corrupt"
    TOO_LARGE = "too-large"
    PERMISSION_DENIED = "permission-denied"
    TIMED_OUT = "timed-out"
    OCR_FAILED = "ocr-failed"
    SKIPPED = "skipped"


class ExtractionMethod(StrEnum):
    NATIVE = "native"
    EMBEDDED = "embedded"
    OCR = "ocr"


@dataclass(frozen=True)
class ManifestJob:
    job_id: str
    document_id: str
    source_id: str
    file_id: str
    content_version: str
    lease_token: str
    share_id: str
    source_path: str
    relative_path: str
    content_fingerprint: str
    pipeline_version: str
    size_bytes: int
    matter_ids: tuple[str, ...] = ()
    detected_mime: str | None = None
    attempt: int = 1


@dataclass(frozen=True)
class Section:
    ordinal: int
    text: str
    method: ExtractionMethod
    page_number: int | None = None
    heading: str | None = None
    source_name: str | None = None
    confidence: float | None = None
    chunk_id: str = ""
    section_path: tuple[str, ...] = ()
    start_offset: int | None = None
    end_offset: int | None = None


@dataclass(frozen=True)
class ExtractionRecord:
    schema_version: int
    job_id: str
    document_id: str
    source_id: str
    file_id: str
    content_version: str
    share_id: str
    relative_path: str
    filename: str
    extension: str
    content_fingerprint: str
    pipeline_version: str
    status: TerminalStatus
    media_type: str | None
    sections: tuple[Section, ...] = ()
    native_text_chars: int = 0
    ocr_pending_pages: tuple[int, ...] = ()
    error_code: str | None = None
    matter_ids: tuple[str, ...] = ()
    acl_tokens: tuple[str, ...] = ()
    acl_state: str = "pending"
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OcrJob:
    job_id: str
    document_id: str
    source_id: str
    file_id: str
    content_version: str
    lease_token: str
    share_id: str
    source_path: str
    relative_path: str
    content_fingerprint: str
    pipeline_version: str
    size_bytes: int
    matter_ids: tuple[str, ...]
    pages: tuple[int, ...]
    languages: tuple[str, ...]


class ManifestQueue(Protocol):
    """Durable manifest/queue adapter owned outside this package.

    Implementations must fence complete/retry/renew operations with the leased
    job's opaque ``lease_token`` and identity tuple
    ``(source_id, file_id, content_version)``. A path is mutable metadata and
    must never substitute for stable file identity.
    """

    def lease_extraction(self, *, lease_seconds: int) -> ManifestJob | None: ...

    def complete_extraction(self, job: ManifestJob, record: ExtractionRecord) -> None: ...

    def enqueue_ocr(self, job: OcrJob) -> None: ...

    def lease_ocr(self, *, lease_seconds: int) -> OcrJob | None: ...

    def renew_ocr(self, job: OcrJob, *, lease_seconds: int) -> None: ...

    def complete_ocr(self, job: OcrJob, record: ExtractionRecord) -> None: ...

    def retry(
        self,
        job: ManifestJob | OcrJob,
        *,
        error_code: str,
        retry_after_seconds: int,
    ) -> None: ...


class SearchSink(Protocol):
    """Idempotent, content-version-fenced sink.

    Acknowledgement is the manifest commit point. Adapters may map sections to
    the FM-03 DocumentChunk/LocalSearchEngine contract, but this package does
    not import or implement that serving engine.
    """

    def publish(self, record: ExtractionRecord) -> None: ...


def deterministic_chunk_id(
    *,
    document_id: str,
    content_version: str,
    ordinal: int,
    page_number: int | None,
    method: ExtractionMethod,
) -> str:
    value = (
        f"{document_id}\x1f{content_version}\x1f{method.value}\x1f{ordinal}\x1f{page_number or 0}"
    ).encode()
    return hashlib.sha256(value).hexdigest()
