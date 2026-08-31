"""Small, source-adapter-neutral writer for the legal document/chunk contract."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from .loader import chunk_text
from .public_lineage import public_authority_metadata, require_public_candidate_version


@dataclass(frozen=True)
class AdapterDocument:
    source_key: str
    external_id: str
    document_type: str
    title: str
    citation: str | None
    jurisdiction: str
    authority_tier: str
    canonical_url: str
    text: str
    effective_date: date | None = None
    publication_date: date | None = None
    source_modified_at: datetime | None = None
    retrieved_at: datetime | None = None
    document_status: str = "current"
    raw_media_type: str = "application/json"
    parser_version: str = "adapter-v1"
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


def upsert_adapter_document(conn: Any, document: AdapterDocument) -> dict[str, Any]:
    """Upsert one adapter result and replace chunks only when its content changed.

    The parent source must have been seeded in ``legal_sources`` before this is called.
    Keeping the write here makes adapters testable without coupling them to a catalog edit.
    """
    corpus_version = require_public_candidate_version(
        conn,
        source_key=document.source_key,
        error_message=(
            "authority adapter requires a staged release with current reviewed "
            "public-source lineage"
        ),
    )
    metadata = public_authority_metadata(document.metadata)
    with conn.cursor() as cursor:
        cursor.execute(
            """SELECT id, content_hash FROM legal_documents
                WHERE source_key=%s AND external_id=%s AND corpus_version=%s""",
            [document.source_key, document.external_id, corpus_version],
        )
        existing = cursor.fetchone()
        changed = existing is None or existing[1] != document.content_hash
        cursor.execute(
            """
            INSERT INTO legal_documents (
                source_key, external_id, document_type, title, citation, jurisdiction,
                authority_tier, document_status, publication_date, effective_date,
                canonical_url, source_modified_at, retrieved_at, content_hash,
                raw_media_type, parser_version, text_content, metadata,
                public_namespace, corpus_version
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,COALESCE(%s,now()),%s,%s,%s,%s,%s::jsonb,%s,%s)
            ON CONFLICT (source_key, external_id, corpus_version) DO UPDATE SET
                document_type=EXCLUDED.document_type, title=EXCLUDED.title,
                citation=EXCLUDED.citation, jurisdiction=EXCLUDED.jurisdiction,
                authority_tier=EXCLUDED.authority_tier, document_status=EXCLUDED.document_status,
                publication_date=EXCLUDED.publication_date, effective_date=EXCLUDED.effective_date,
                canonical_url=EXCLUDED.canonical_url, source_modified_at=EXCLUDED.source_modified_at,
                retrieved_at=EXCLUDED.retrieved_at, content_hash=EXCLUDED.content_hash,
                raw_media_type=EXCLUDED.raw_media_type, parser_version=EXCLUDED.parser_version,
                text_content=EXCLUDED.text_content,
                metadata=legal_documents.metadata || EXCLUDED.metadata,
                updated_at=now() RETURNING id
            """,
            [
                document.source_key,
                document.external_id,
                document.document_type,
                document.title,
                document.citation,
                document.jurisdiction,
                document.authority_tier,
                document.document_status,
                document.publication_date,
                document.effective_date,
                document.canonical_url,
                document.source_modified_at,
                document.retrieved_at,
                document.content_hash,
                document.raw_media_type,
                document.parser_version,
                document.text,
                json.dumps(metadata),
                "public-authority",
                corpus_version,
            ],
        )
        document_id = cursor.fetchone()[0]
        chunks_created = 0
        if changed:
            cursor.execute(
                "DELETE FROM legal_document_chunks WHERE document_id=%s", [document_id]
            )
            for index, content in enumerate(chunk_text(document.text)):
                cursor.execute(
                    """INSERT INTO legal_document_chunks
                    (document_id, chunk_index, content, content_hash, embedding,
                     embedding_version, metadata, corpus_version)
                    VALUES (%s,%s,%s,%s,NULL,0,%s::jsonb,%s)""",
                    [
                        document_id,
                        index,
                        content,
                        hashlib.sha256(content.encode("utf-8")).hexdigest(),
                        json.dumps(metadata),
                        corpus_version,
                    ],
                )
                chunks_created += 1
    return {
        "external_id": document.external_id,
        "corpus_version": corpus_version,
        "changed": changed,
        "chunks_created": chunks_created,
    }


def refresh_source_status(
    conn: Any, source_keys: set[str] | list[str] | tuple[str, ...]
) -> None:
    """Refresh operator-visible freshness/count state after a successful batch."""
    with conn.cursor() as cursor:
        for source_key in sorted(set(source_keys)):
            cursor.execute(
                """UPDATE legal_sources
                   SET last_attempted_at=now(), last_successful_sync_at=now(),
                       current_error=NULL,
                       item_count=(SELECT COUNT(*) FROM legal_documents WHERE source_key=%s),
                       chunk_count=(SELECT COUNT(*) FROM legal_document_chunks c
                                    JOIN legal_documents d ON d.id=c.document_id
                                    WHERE d.source_key=%s),
                       embedded_chunk_count=(SELECT COUNT(*) FROM legal_document_chunks c
                                             JOIN legal_documents d ON d.id=c.document_id
                                             WHERE d.source_key=%s AND c.embedding IS NOT NULL),
                       updated_at=now()
                   WHERE source_key=%s""",
                [source_key, source_key, source_key, source_key],
            )
