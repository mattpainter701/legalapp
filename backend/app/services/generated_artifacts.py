"""Canonical persistence for generated legal work products and revisions."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.generated_artifact import GeneratedArtifact, GeneratedArtifactRevision

MAX_ARTIFACT_CONTENT_CHARS = 50_000
MAX_ARTIFACT_SOURCES = 10
MAX_ARTIFACT_VARIABLE_JSON_CHARS = 20_000
ARTIFACT_RENDERER_VERSION = "lawhand-markdown-docx-v1"
ALLOWED_ARTIFACT_CHANNELS = frozenset({"matter_chat", "workspace_mcp"})
ALLOWED_TEMPLATE_FORMATS = frozenset({"markdown", "docx", "pdf"})


class GeneratedArtifactError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class GeneratedArtifactResult:
    artifact: GeneratedArtifact
    revision: GeneratedArtifactRevision
    created: bool


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_artifact_request_sha256(payload: dict[str, Any]) -> str:
    """Hash the semantic proposal, independent of JSON key ordering."""

    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return _sha256_text(encoded)


def derive_artifact_request_id(
    *,
    tenant_id: uuid.UUID,
    channel: str,
    explicit_request_id: uuid.UUID | None,
    transport_request_id: str | None,
) -> uuid.UUID:
    """Use a caller key when present; otherwise derive or create one server-side."""

    if explicit_request_id is not None:
        return explicit_request_id
    if transport_request_id:
        return uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"lawhand:{tenant_id}:{channel}:{transport_request_id.strip()}",
        )
    return uuid.uuid4()


def _bounded_source_snapshot(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    bounded: list[dict[str, Any]] = []
    for source in sources[:MAX_ARTIFACT_SOURCES]:
        source_id = str(source.get("source_id") or "").strip()[:500]
        if not source_id:
            continue
        item: dict[str, Any] = {"source_id": source_id}
        label = str(source.get("label") or "").strip()
        if label:
            item["label"] = label[:500]
        for key, limit in (
            ("source_type", 80),
            ("citation", 300),
            ("locator", 300),
        ):
            value = str(source.get(key) or "").strip()
            if value:
                item[key] = value[:limit]
        sha256 = (
            str(source.get("sha256") or source.get("document_sha256") or "")
            .strip()
            .casefold()
        )
        if len(sha256) == 64 and all(char in "0123456789abcdef" for char in sha256):
            item["sha256"] = sha256
        bounded.append(item)
    return bounded


def _validated_variable_snapshot(value: dict[str, Any] | None) -> dict[str, Any]:
    snapshot = value or {}
    if not isinstance(snapshot, dict):
        raise GeneratedArtifactError(
            "invalid_template_variables", "Template variables must be an object"
        )
    try:
        encoded = json.dumps(
            snapshot,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    except (TypeError, ValueError) as exc:
        raise GeneratedArtifactError(
            "invalid_template_variables", "Template variables could not be recorded"
        ) from exc
    if len(encoded) > MAX_ARTIFACT_VARIABLE_JSON_CHARS:
        raise GeneratedArtifactError(
            "invalid_template_variables", "Template variables exceed the safe limit"
        )
    return json.loads(encoded)


def _valid_sha256(value: str | None) -> bool:
    normalized = str(value or "").strip().casefold()
    return len(normalized) == 64 and all(
        char in "0123456789abcdef" for char in normalized
    )


async def create_initial_generated_artifact(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    matter_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    conversation_id: uuid.UUID | None,
    title: str,
    kind: str,
    content_text: str,
    source_channel: str,
    client_request_id: uuid.UUID,
    request_payload: dict[str, Any],
    sources: list[dict[str, Any]],
    template_id: uuid.UUID | None = None,
    template_sha256: str | None = None,
    template_format: str | None = None,
    variable_snapshot: dict[str, Any] | None = None,
    unresolved_variables: list[str] | None = None,
) -> GeneratedArtifactResult:
    """Idempotently claim one request and create immutable revision one."""

    content = content_text.strip()
    if not content or len(content) > MAX_ARTIFACT_CONTENT_CHARS:
        raise GeneratedArtifactError(
            "invalid_artifact_content",
            "Generated document content is empty or exceeds the safe limit",
        )
    clean_title = title.strip()
    clean_kind = kind.strip()
    if (
        not clean_title
        or len(clean_title) > 300
        or not clean_kind
        or len(clean_kind) > 80
    ):
        raise GeneratedArtifactError(
            "invalid_artifact_metadata", "Generated document title or kind is invalid"
        )
    if source_channel not in ALLOWED_ARTIFACT_CHANNELS:
        raise GeneratedArtifactError(
            "invalid_source_channel", "Generated document source channel is invalid"
        )
    normalized_template_format = str(template_format or "").strip().casefold() or None
    if (
        normalized_template_format is not None
        and normalized_template_format not in ALLOWED_TEMPLATE_FORMATS
    ):
        raise GeneratedArtifactError(
            "unsupported_template_format", "Template format is not supported"
        )
    normalized_template_sha256 = str(template_sha256 or "").strip().casefold() or None
    if normalized_template_sha256 is not None and not _valid_sha256(
        normalized_template_sha256
    ):
        raise GeneratedArtifactError(
            "invalid_template_hash", "Template fingerprint is invalid"
        )
    variables = _validated_variable_snapshot(variable_snapshot)
    unresolved = list(
        dict.fromkeys(
            str(item).strip()[:120]
            for item in (unresolved_variables or [])
            if str(item).strip()
        )
    )[:100]
    source_snapshot = _bounded_source_snapshot(sources)
    semantic_request = {
        **request_payload,
        "source_channel": source_channel,
        "source_snapshot": source_snapshot,
        "template_id": str(template_id) if template_id else None,
        "template_sha256": normalized_template_sha256,
        "template_format": normalized_template_format,
        "variable_snapshot": variables,
        "unresolved_variables": unresolved,
    }
    request_sha256 = canonical_artifact_request_sha256(semantic_request)
    artifact_id = uuid.uuid4()
    provenance = {
        "source_channel": source_channel,
        "source_ids": [item["source_id"] for item in source_snapshot],
        "sources": source_snapshot,
    }
    claimed_id = (
        await db.execute(
            pg_insert(GeneratedArtifact)
            .values(
                id=artifact_id,
                tenant_id=tenant_id,
                matter_id=matter_id,
                conversation_id=conversation_id,
                created_by_user_id=actor_user_id,
                title=clean_title,
                kind=clean_kind,
                format="docx",
                status="draft",
                current_revision_no=1,
                source_channel=source_channel,
                client_request_id=client_request_id,
                request_sha256=request_sha256,
                provenance=provenance,
            )
            .on_conflict_do_nothing(constraint="uq_generated_artifacts_tenant_request")
            .returning(GeneratedArtifact.id)
        )
    ).scalar_one_or_none()

    artifact = await db.scalar(
        select(GeneratedArtifact).where(
            GeneratedArtifact.tenant_id == tenant_id,
            GeneratedArtifact.client_request_id == client_request_id,
        )
    )
    if artifact is None:
        raise GeneratedArtifactError(
            "artifact_persistence_failed", "Generated artifact could not be created"
        )
    if artifact.request_sha256 != request_sha256:
        raise GeneratedArtifactError(
            "idempotency_conflict",
            "This idempotency key was already used for a different document proposal",
        )

    if claimed_id is None:
        revision = await db.scalar(
            select(GeneratedArtifactRevision).where(
                GeneratedArtifactRevision.tenant_id == tenant_id,
                GeneratedArtifactRevision.artifact_id == artifact.id,
                GeneratedArtifactRevision.revision_no == artifact.current_revision_no,
            )
        )
        if revision is None:
            raise GeneratedArtifactError(
                "artifact_incomplete", "Generated artifact revision is unavailable"
            )
        return GeneratedArtifactResult(artifact, revision, False)

    revision = GeneratedArtifactRevision(
        tenant_id=tenant_id,
        artifact_id=artifact.id,
        revision_no=1,
        content_text=content,
        content_sha256=_sha256_text(content),
        template_id=template_id,
        template_sha256=normalized_template_sha256,
        template_format=normalized_template_format,
        variable_snapshot=variables,
        unresolved_variables=unresolved,
        source_snapshot=source_snapshot,
        renderer_version=ARTIFACT_RENDERER_VERSION,
        created_by_user_id=actor_user_id,
    )
    db.add(revision)
    await db.flush()
    return GeneratedArtifactResult(artifact, revision, True)


async def create_generated_artifact_revision(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    artifact_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    expected_revision_no: int,
    content_text: str,
    title: str | None = None,
) -> GeneratedArtifactRevision:
    """Append a revision under an optimistic lock; prior rows stay immutable."""

    content = content_text.strip()
    if not content or len(content) > MAX_ARTIFACT_CONTENT_CHARS:
        raise GeneratedArtifactError(
            "invalid_artifact_content",
            "Generated document content is empty or exceeds the safe limit",
        )
    artifact = await db.scalar(
        select(GeneratedArtifact)
        .where(
            GeneratedArtifact.id == artifact_id,
            GeneratedArtifact.tenant_id == tenant_id,
        )
        .with_for_update()
    )
    if artifact is None:
        raise GeneratedArtifactError("artifact_not_found", "Artifact not found")
    if artifact.status not in {"draft", "review"}:
        raise GeneratedArtifactError(
            "artifact_not_editable", "Only a draft under review can be revised"
        )
    if artifact.current_revision_no != expected_revision_no:
        raise GeneratedArtifactError(
            "artifact_version_conflict",
            "The artifact changed after it was loaded; review the latest revision",
        )
    parent = await db.scalar(
        select(GeneratedArtifactRevision).where(
            GeneratedArtifactRevision.tenant_id == tenant_id,
            GeneratedArtifactRevision.artifact_id == artifact.id,
            GeneratedArtifactRevision.revision_no == expected_revision_no,
        )
    )
    if parent is None:
        raise GeneratedArtifactError(
            "artifact_incomplete", "Generated artifact revision is unavailable"
        )
    next_revision = GeneratedArtifactRevision(
        tenant_id=tenant_id,
        artifact_id=artifact.id,
        parent_revision_id=parent.id,
        revision_no=expected_revision_no + 1,
        content_text=content,
        content_sha256=_sha256_text(content),
        template_id=parent.template_id,
        template_sha256=parent.template_sha256,
        template_format=parent.template_format,
        variable_snapshot=parent.variable_snapshot or {},
        unresolved_variables=parent.unresolved_variables or [],
        source_snapshot=parent.source_snapshot or [],
        renderer_version=parent.renderer_version,
        model_metadata=parent.model_metadata,
        created_by_user_id=actor_user_id,
    )
    db.add(next_revision)
    artifact.current_revision_no = next_revision.revision_no
    if title is not None:
        artifact.title = title
    await db.flush()
    return next_revision
