"""Authoritative Template Studio draft transaction and redaction boundary."""

from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.document_template import DocumentTemplate
from app.models.studio_draft import (
    StudioDraft,
    StudioDraftAuditEvent,
    StudioDraftField,
    StudioDraftIdempotency,
    StudioDraftPlacement,
    StudioDraftSnapshot,
    StudioSourceArtifact,
)
from app.schemas.studio_draft import (
    StudioDraftCreate,
    StudioDraftImport,
    StudioDraftPatch,
    StudioPromoteRequest,
    StudioRevisionRequest,
    canonical_placement_anchor,
)
from app.services.automation_capabilities import CapabilityError
from app.services.docx_templates import TemplateDocxError, validate_docx_package
from app.services.document_template_workspace import verified_template_source
from app.services.pdf_templates import TemplatePdfError, pdf_page_metadata
from app.services.template_intake import _docx_has_tracked_changes

MAX_JSON_BYTES = 256_000
MAX_JSON_DEPTH = 7
MAX_JSON_NODES = 5_000
_FORBIDDEN_DURABLE_KEYS = {
    "body",
    "content",
    "document_text",
    "raw_text",
    "source_text",
    "value",
    "values",
    "default",
    "example",
    "provider_path",
    "provider_id",
    "storage_path",
    "signed_url",
    "download_url",
    "source_url",
    "item_id",
    "drive_id",
}
_RESERVED_FIELD_DEFINITION_KEYS = {
    "id",
    "studio_field_id",
    "name",
    "key",
    "automation_key",
    "label",
    "type",
    "field_type",
    "required",
    "position",
    "placements",
}
_ALLOWED_FIELD_DEFINITION_KEYS = {
    "choices",
    "confidence",
    "constraints",
    "date_format",
    "display",
    "included",
    "max_length",
    "maximum",
    "min_length",
    "minimum",
    "multiline",
    "number_format",
    "option_labels",
    "pattern",
    "readonly",
    "semantic_type",
    "validation",
}
_SOURCE_RESOLVER_PATTERN = re.compile(
    r"^studio-db:v1:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_CANONICAL_SOURCE_MEDIA = {
    "markdown": "text/markdown",
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
_ACCEPTED_SOURCE_MEDIA = {
    "markdown": {"text/markdown", "text/plain"},
    "pdf": {"application/pdf"},
    "docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
}


class StudioError(Exception):
    def __init__(self, status_code: int, detail: dict[str, Any] | str):
        super().__init__(str(detail))
        self.status_code = status_code
        self.detail = detail


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        jsonable_encoder(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _etag(draft: StudioDraft) -> str:
    return f'"studio:{draft.id}:{draft.revision}:{draft.identity_sha256}"'


def _bounded_redacted(value: Any) -> Any:
    """Reject unsafe or unbounded JSON before it reaches snapshots/audit rows."""

    nodes = 0

    def walk(item: Any, depth: int) -> Any:
        nonlocal nodes
        nodes += 1
        if depth > MAX_JSON_DEPTH or nodes > MAX_JSON_NODES:
            raise StudioError(
                422,
                {
                    "code": "payload_too_complex",
                    "message": "JSON payload exceeds structural limits",
                },
            )
        if isinstance(item, dict):
            clean: dict[str, Any] = {}
            for raw_key, child in item.items():
                key = str(raw_key)
                if key.lower() in _FORBIDDEN_DURABLE_KEYS:
                    raise StudioError(
                        422,
                        {
                            "code": "unsafe_durable_payload",
                            "message": f"durable key '{key}' is not allowed",
                        },
                    )
                clean[key] = walk(child, depth + 1)
            return clean
        if isinstance(item, list):
            if len(item) > 1000:
                raise StudioError(
                    422,
                    {
                        "code": "payload_too_large",
                        "message": "array exceeds item limit",
                    },
                )
            return [walk(child, depth + 1) for child in item]
        if isinstance(item, str) and len(item) > 2000:
            raise StudioError(
                422,
                {
                    "code": "payload_too_large",
                    "message": "string exceeds durable limit",
                },
            )
        if isinstance(item, float) and not math.isfinite(item):
            raise StudioError(
                422,
                {
                    "code": "non_finite_number",
                    "message": "durable numbers must be finite",
                },
            )
        if item is None or isinstance(item, (str, int, float, bool)):
            return item
        raise StudioError(
            422,
            {
                "code": "unsupported_payload_type",
                "message": "payload must be JSON-compatible",
            },
        )

    result = walk(jsonable_encoder(value), 0)
    if len(_canonical_json(result)) > MAX_JSON_BYTES:
        raise StudioError(
            422,
            {"code": "payload_too_large", "message": "JSON payload exceeds byte limit"},
        )
    return result


def _field_definition(value: dict[str, Any]) -> dict[str, Any]:
    overlap = _RESERVED_FIELD_DEFINITION_KEYS.intersection(value)
    if overlap:
        raise StudioError(
            422,
            {
                "code": "reserved_field_definition_key",
                "message": f"field definition cannot override '{sorted(overlap)[0]}'",
            },
        )
    unknown = set(value).difference(_ALLOWED_FIELD_DEFINITION_KEYS)
    if unknown:
        raise StudioError(
            422,
            {
                "code": "unsupported_field_definition_key",
                "message": f"field definition key '{sorted(unknown)[0]}' is not supported",
            },
        )
    return _bounded_redacted(value)


def source_contract(draft: StudioDraft) -> dict[str, Any]:
    """Return the only source locator workers may receive from this domain."""

    return {
        "contract_version": 1,
        "artifact_id": draft.source_artifact_id,
        "sha256": draft.source_sha256,
        "media_type": draft.source_media_type,
        "format": draft.format,
    }


def _published_template_base(template: DocumentTemplate) -> str:
    source_sha = (
        template.source_sha256
        or hashlib.sha256(str(template.body or "").encode("utf-8")).hexdigest()
    )
    return _sha256(
        {
            "title": template.title,
            "status": template.status,
            "variable_schema": template.variable_schema or {},
            "format": template.format or "markdown",
            "source_sha256": source_sha,
            "source_content_type": template.source_content_type,
            "source_file_size": template.source_file_size,
            "body_sha256": hashlib.sha256(
                str(template.body or "").encode("utf-8")
            ).hexdigest(),
        }
    )


async def _verified_compatibility_source(template: DocumentTemplate) -> bytes:
    if template.source_storage_path:
        try:
            return await verified_template_source(template)
        except CapabilityError as exc:
            raise StudioError(409, {"code": exc.code, "message": exc.message}) from exc
    content = str(template.body or "").encode("utf-8")
    digest = hashlib.sha256(content).hexdigest()
    if template.source_sha256 and template.source_sha256 != digest:
        raise StudioError(
            409,
            {
                "code": "template_integrity_failed",
                "message": "published template body failed its integrity check",
            },
        )
    return content


def _canonical_source_bytes(
    content: bytes, format_name: str, claimed_media_type: str
) -> tuple[bytes, str]:
    """Validate bytes independently of MIME and return the canonical contract."""

    canonical_media = _CANONICAL_SOURCE_MEDIA.get(format_name)
    normalized_media = str(claimed_media_type or "").split(";", 1)[0].strip().lower()
    if canonical_media is None or normalized_media not in _ACCEPTED_SOURCE_MEDIA.get(
        format_name, set()
    ):
        raise StudioError(
            422,
            {
                "code": "source_format_media_mismatch",
                "message": "source media type is not allowed for the selected format",
            },
        )
    try:
        if format_name == "pdf":
            pdf_page_metadata(content)
        elif format_name == "docx":
            validate_docx_package(content)
            if _docx_has_tracked_changes(content):
                raise TemplateDocxError(
                    "Word documents with tracked changes are not supported as reusable templates."
                )
        else:
            text_value = content.decode("utf-8")
            if any(
                ord(character) < 32 and character not in {"\n", "\r", "\t"}
                for character in text_value
            ):
                raise UnicodeError("markdown source contains invalid control text")
            text_value = text_value.replace("\r\n", "\n").replace("\r", "\n")
            content = text_value.encode("utf-8")
    except (TemplatePdfError, TemplateDocxError, UnicodeError) as exc:
        raise StudioError(
            422,
            {
                "code": "invalid_source_content",
                "message": str(exc) or "source content failed format validation",
            },
        ) from exc
    return bytes(content), canonical_media


class StudioSourceRegistry:
    """Server-owned, tenant-scoped registration and verified source read boundary."""

    def __init__(
        self, db: AsyncSession, tenant_id: uuid.UUID, actor_user_id: uuid.UUID
    ):
        self.db = db
        self.tenant_id = tenant_id
        self.actor_user_id = actor_user_id
        self.settings = get_settings()

    async def _admission_lock(self) -> None:
        await self.db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
            {"scope": f"studio-source-admission:{self.tenant_id}"},
        )

    async def register(
        self, content: bytes, format_name: str, claimed_media_type: str
    ) -> StudioSourceArtifact:
        max_bytes = self.settings.MAX_FILE_SIZE_MB * 1024 * 1024
        if not content or len(content) > max_bytes:
            raise StudioError(
                413,
                {
                    "code": "source_size_invalid",
                    "message": "source bytes must be non-empty and within the upload limit",
                },
            )
        content, canonical_media = _canonical_source_bytes(
            content, format_name, claimed_media_type
        )
        digest = hashlib.sha256(content).hexdigest()
        await self._admission_lock()
        existing = await self.db.scalar(
            select(StudioSourceArtifact).where(
                StudioSourceArtifact.tenant_id == self.tenant_id,
                StudioSourceArtifact.sha256 == digest,
                StudioSourceArtifact.media_type == canonical_media,
                StudioSourceArtifact.format == format_name,
            )
        )
        if existing is not None:
            await self.read(
                existing.id,
                expected_sha256=digest,
                expected_media_type=canonical_media,
                expected_format=format_name,
            )
            return existing
        artifact_count, aggregate_bytes = (
            await self.db.execute(
                select(
                    func.count(StudioSourceArtifact.id),
                    func.coalesce(func.sum(StudioSourceArtifact.byte_size), 0),
                ).where(StudioSourceArtifact.tenant_id == self.tenant_id)
            )
        ).one()
        if artifact_count >= self.settings.TEMPLATE_STUDIO_SOURCE_ARTIFACT_QUOTA:
            raise StudioError(
                429,
                {
                    "code": "source_artifact_quota_exceeded",
                    "message": "tenant source artifact quota reached",
                },
            )
        if (
            int(aggregate_bytes or 0) + len(content)
            > self.settings.TEMPLATE_STUDIO_SOURCE_BYTES_QUOTA
        ):
            raise StudioError(
                429,
                {
                    "code": "source_bytes_quota_exceeded",
                    "message": "tenant source byte quota reached",
                },
            )
        artifact_id = uuid.uuid4()
        artifact = StudioSourceArtifact(
            id=artifact_id,
            tenant_id=self.tenant_id,
            sha256=digest,
            media_type=canonical_media,
            format=format_name,
            byte_size=len(content),
            resolver_key=f"studio-db:v1:{uuid.uuid4()}",
            content_bytes=bytes(content),
            created_by_user_id=self.actor_user_id,
        )
        self.db.add(artifact)
        await self.db.flush()
        return artifact

    async def resolve(self, artifact_id: uuid.UUID) -> StudioSourceArtifact:
        artifact = await self.db.scalar(
            select(StudioSourceArtifact).where(
                StudioSourceArtifact.id == artifact_id,
                StudioSourceArtifact.tenant_id == self.tenant_id,
            )
        )
        if artifact is None:
            raise StudioError(404, "Template Studio source artifact not found")
        return artifact

    async def read(
        self,
        artifact_id: uuid.UUID,
        *,
        expected_sha256: str | None = None,
        expected_media_type: str | None = None,
        expected_format: str | None = None,
    ) -> bytes:
        artifact = await self.resolve(artifact_id)
        content = bytes(artifact.content_bytes or b"")
        digest = hashlib.sha256(content).hexdigest()
        try:
            canonical_content, canonical_media = _canonical_source_bytes(
                content, artifact.format, artifact.media_type
            )
        except StudioError as exc:
            raise StudioError(
                409,
                {
                    "code": "source_integrity_failed",
                    "message": "registered source failed format validation",
                },
            ) from exc
        if (
            not _SOURCE_RESOLVER_PATTERN.fullmatch(artifact.resolver_key or "")
            or artifact.byte_size != len(content)
            or artifact.sha256 != digest
            or canonical_content != content
            or canonical_media != artifact.media_type
            or (expected_sha256 is not None and expected_sha256 != digest)
            or (
                expected_media_type is not None
                and expected_media_type != artifact.media_type
            )
            or (expected_format is not None and expected_format != artifact.format)
        ):
            raise StudioError(
                409,
                {
                    "code": "source_integrity_failed",
                    "message": "registered source failed its integrity contract",
                },
            )
        return content

    @staticmethod
    def contract(artifact: StudioSourceArtifact) -> dict[str, Any]:
        return {
            "contract_version": 1,
            "artifact_id": artifact.id,
            "sha256": artifact.sha256,
            "media_type": artifact.media_type,
            "format": artifact.format,
        }

    async def purge_expired_orphans(self, *, limit: int = 100) -> int:
        """Bounded caller-owned cleanup seam; Phase 2 wires no scheduler."""

        if not 1 <= limit <= 500:
            raise ValueError("orphan cleanup limit must be between 1 and 500")
        await self._admission_lock()
        cutoff = datetime.now(timezone.utc) - timedelta(
            hours=self.settings.TEMPLATE_STUDIO_SOURCE_ORPHAN_TTL_HOURS
        )
        await authorize_studio_orphan_cleanup(self.db, self.tenant_id, cutoff)
        referenced = select(StudioDraft.id).where(
            StudioDraft.tenant_id == self.tenant_id,
            StudioDraft.source_artifact_id == StudioSourceArtifact.id,
        )
        rows = list(
            (
                await self.db.scalars(
                    select(StudioSourceArtifact)
                    .where(
                        StudioSourceArtifact.tenant_id == self.tenant_id,
                        StudioSourceArtifact.created_at <= cutoff,
                        ~referenced.exists(),
                    )
                    .order_by(StudioSourceArtifact.created_at, StudioSourceArtifact.id)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            ).all()
        )
        for row in rows:
            await self.db.delete(row)
        await self.db.flush()
        return len(rows)


async def authorize_studio_demo_purge(
    db: AsyncSession, tenant_id: uuid.UUID, demo_session_id: uuid.UUID
) -> None:
    """Present demo claim identifiers; the trigger verifies authoritative rows."""

    await db.execute(
        text("SELECT set_config('app.studio_demo_purge_tenant_id', :tenant, true)"),
        {"tenant": str(tenant_id)},
    )
    await db.execute(
        text("SELECT set_config('app.studio_demo_purge_session_id', :session, true)"),
        {"session": str(demo_session_id)},
    )


async def authorize_studio_orphan_cleanup(
    db: AsyncSession, tenant_id: uuid.UUID, cutoff: datetime
) -> None:
    """Present a bounded orphan cutoff; the trigger verifies age and references."""

    await db.execute(
        text("SELECT set_config('app.studio_orphan_cleanup_tenant_id', :tenant, true)"),
        {"tenant": str(tenant_id)},
    )
    await db.execute(
        text("SELECT set_config('app.studio_orphan_cleanup_cutoff', :cutoff, true)"),
        {"cutoff": cutoff.isoformat()},
    )


class StudioDraftService:
    """Owns all draft reads/mutations and their transaction boundaries.

    Phase 4 must call these methods rather than mutating ORM rows. Proposal
    persistence intentionally remains outside revision 147; proposal acceptance
    must translate its bounded operations into ``patch`` and therefore creates
    exactly one new draft revision.
    """

    def __init__(
        self, db: AsyncSession, tenant_id: uuid.UUID, actor_user_id: uuid.UUID
    ):
        self.db = db
        self.tenant_id = tenant_id
        self.actor_user_id = actor_user_id
        self.settings = get_settings()
        self.sources = StudioSourceRegistry(db, tenant_id, actor_user_id)

    async def _idempotency(
        self, operation: str, key: str, request: Any
    ) -> tuple[StudioDraftIdempotency | None, dict[str, Any] | None]:
        request_hash = _sha256(request)
        scope = f"studio:{self.tenant_id}:{self.actor_user_id}:{operation}:{key}"
        await self.db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
            {"scope": scope},
        )
        row = await self.db.scalar(
            select(StudioDraftIdempotency).where(
                StudioDraftIdempotency.tenant_id == self.tenant_id,
                StudioDraftIdempotency.actor_user_id == self.actor_user_id,
                StudioDraftIdempotency.operation == operation,
                StudioDraftIdempotency.idempotency_key == key,
            )
        )
        if row:
            if row.request_sha256 != request_hash:
                raise StudioError(
                    409,
                    {
                        "code": "idempotency_key_mismatch",
                        "message": "idempotency key was already used for a different request",
                    },
                )
            if row.response_json is not None:
                return None, row.response_json
            raise StudioError(
                409,
                {
                    "code": "request_in_progress",
                    "message": "matching request is still in progress",
                },
            )
        reservation = StudioDraftIdempotency(
            tenant_id=self.tenant_id,
            actor_user_id=self.actor_user_id,
            operation=operation,
            idempotency_key=key,
            request_sha256=request_hash,
            expires_at=datetime.now(timezone.utc)
            + timedelta(hours=self.settings.TEMPLATE_STUDIO_IDEMPOTENCY_TTL_HOURS),
        )
        self.db.add(reservation)
        await self.db.flush()
        return reservation, None

    @staticmethod
    def _finish_idempotency(
        reservation: StudioDraftIdempotency | None, response: dict[str, Any]
    ) -> None:
        if reservation is not None:
            reservation.response_json = jsonable_encoder(response)

    async def _locked_draft(self, draft_id: uuid.UUID) -> StudioDraft:
        draft = await self.db.scalar(
            select(StudioDraft)
            .where(StudioDraft.id == draft_id, StudioDraft.tenant_id == self.tenant_id)
            .with_for_update()
        )
        if draft is None:
            raise StudioError(404, "Template Studio draft not found")
        return draft

    async def register_source(
        self, content: bytes, format_name: str, claimed_media_type: str
    ) -> dict[str, Any]:
        artifact = await self.sources.register(content, format_name, claimed_media_type)
        response = self.sources.contract(artifact)
        await self.db.commit()
        return response

    async def read_source_bytes(
        self,
        artifact_id: uuid.UUID,
        *,
        expected_sha256: str | None = None,
        expected_media_type: str | None = None,
        expected_format: str | None = None,
    ) -> bytes:
        """Authoritative internal worker read with tenant and integrity checks."""

        return await self.sources.read(
            artifact_id,
            expected_sha256=expected_sha256,
            expected_media_type=expected_media_type,
            expected_format=expected_format,
        )

    async def purge_expired_source_orphans(self, *, limit: int = 100) -> int:
        deleted = await self.sources.purge_expired_orphans(limit=limit)
        await self.db.commit()
        return deleted

    async def _tenant_admission_lock(self) -> None:
        await self.db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
            {"scope": f"studio-active-draft-admission:{self.tenant_id}"},
        )

    async def _enforce_active_quota(self) -> None:
        count = await self.db.scalar(
            select(func.count())
            .select_from(StudioDraft)
            .where(
                StudioDraft.tenant_id == self.tenant_id,
                StudioDraft.lifecycle_state == "active",
            )
        )
        if (count or 0) >= self.settings.TEMPLATE_STUDIO_ACTIVE_DRAFT_QUOTA:
            raise StudioError(
                429,
                {
                    "code": "draft_quota_exceeded",
                    "message": "tenant active draft quota reached",
                },
            )

    @staticmethod
    def _check_revision(draft: StudioDraft, expected: int) -> None:
        if draft.revision != expected:
            raise StudioError(
                409,
                {
                    "code": "stale_revision",
                    "message": "draft changed since the supplied revision",
                    "expected_revision": expected,
                    "current_revision": draft.revision,
                    "current_etag": _etag(draft),
                },
            )

    async def _parts(
        self, draft_id: uuid.UUID
    ) -> tuple[list[StudioDraftField], list[StudioDraftPlacement]]:
        fields = list(
            (
                await self.db.scalars(
                    select(StudioDraftField)
                    .where(
                        StudioDraftField.tenant_id == self.tenant_id,
                        StudioDraftField.draft_id == draft_id,
                    )
                    .order_by(StudioDraftField.position, StudioDraftField.id)
                )
            ).all()
        )
        placements = list(
            (
                await self.db.scalars(
                    select(StudioDraftPlacement)
                    .where(
                        StudioDraftPlacement.tenant_id == self.tenant_id,
                        StudioDraftPlacement.draft_id == draft_id,
                    )
                    .order_by(StudioDraftPlacement.id)
                )
            ).all()
        )
        return fields, placements

    async def _validate_source_contract(self, draft: StudioDraft) -> None:
        artifact = await self.sources.resolve(draft.source_artifact_id)
        if artifact.format != draft.format:
            raise StudioError(
                422,
                {
                    "code": "source_format_mismatch",
                    "message": "persisted source format does not match the draft",
                },
            )
        await self.sources.read(
            artifact.id,
            expected_sha256=draft.source_sha256,
            expected_media_type=draft.source_media_type,
            expected_format=draft.format,
        )

    @staticmethod
    def _validate_parts(
        draft: StudioDraft,
        fields: list[StudioDraftField],
        placements: list[StudioDraftPlacement],
        *,
        raise_on_invalid: bool = False,
    ) -> list[dict[str, str]]:
        """Apply the canonical persisted-state validator at every boundary."""

        issues: list[dict[str, str]] = []
        keys: set[str] = set()
        for field in fields:
            if field.automation_key in keys:
                issues.append(
                    {"code": "duplicate_automation_key", "field_id": str(field.id)}
                )
            keys.add(field.automation_key)
            try:
                _field_definition(field.definition or {})
            except StudioError:
                issues.append(
                    {"code": "invalid_field_definition", "field_id": str(field.id)}
                )
        field_ids = {field.id for field in fields}
        for placement in placements:
            if placement.field_id not in field_ids:
                issues.append(
                    {"code": "orphan_placement", "placement_id": str(placement.id)}
                )
            if placement.format != draft.format:
                issues.append(
                    {
                        "code": "placement_format_mismatch",
                        "placement_id": str(placement.id),
                    }
                )
            try:
                canonical = canonical_placement_anchor(
                    placement.format, placement.anchor_kind, placement.anchor
                )
                if canonical != placement.anchor:
                    issues.append(
                        {
                            "code": "noncanonical_placement",
                            "placement_id": str(placement.id),
                        }
                    )
            except (TypeError, ValueError, ValidationError):
                issues.append(
                    {
                        "code": "invalid_placement_contract",
                        "placement_id": str(placement.id),
                    }
                )
        if raise_on_invalid and issues:
            raise StudioError(
                422,
                {
                    "code": "draft_validation_failed",
                    "message": "draft contains an invalid field or placement contract",
                    "issues": issues,
                },
            )
        return issues

    @staticmethod
    def _identity_payload(
        draft: StudioDraft,
        fields: list[StudioDraftField],
        placements: list[StudioDraftPlacement],
    ) -> dict[str, Any]:
        return {
            "version": 1,
            "source": {
                "artifact_id": str(draft.source_artifact_id),
                "sha256": draft.source_sha256,
                "media_type": draft.source_media_type,
            },
            "format": draft.format,
            "fields": [
                {
                    "id": str(item.id),
                    "automation_key": item.automation_key,
                    "label": item.label,
                    "field_type": item.field_type,
                    "required": item.required,
                    "position": item.position,
                    "definition": item.definition,
                }
                for item in fields
            ],
            "placements": [
                {
                    "id": str(item.id),
                    "field_id": str(item.field_id),
                    "format": item.format,
                    "anchor_kind": item.anchor_kind,
                    "anchor": item.anchor,
                }
                for item in placements
            ],
        }

    async def _refresh_identity(self, draft: StudioDraft) -> str:
        fields, placements = await self._parts(draft.id)
        clean = _bounded_redacted(self._identity_payload(draft, fields, placements))
        draft.identity_sha256 = _sha256(clean)
        return draft.identity_sha256

    async def _response(self, draft: StudioDraft) -> dict[str, Any]:
        fields, placements = await self._parts(draft.id)
        return {
            "id": draft.id,
            "title": draft.title,
            "format": draft.format,
            "lifecycle_state": draft.lifecycle_state,
            "revision": draft.revision,
            "identity_sha256": draft.identity_sha256,
            "published_template_id": draft.published_template_id,
            "source": source_contract(draft),
            "fields": [
                {
                    "id": item.id,
                    "automation_key": item.automation_key,
                    "label": item.label,
                    "field_type": item.field_type,
                    "required": item.required,
                    "position": item.position,
                    "definition": item.definition,
                }
                for item in fields
            ],
            "placements": [
                {
                    "id": item.id,
                    "field_id": item.field_id,
                    "format": item.format,
                    "anchor_kind": item.anchor_kind,
                    "anchor": item.anchor,
                }
                for item in placements
            ],
            "evidence_revision": draft.evidence_revision,
            "evidence_invalidated": draft.evidence_revision != draft.revision,
            "cancellation_requested": draft.cancellation_requested_at is not None,
            "etag": _etag(draft),
        }

    def _audit(
        self,
        draft: StudioDraft,
        event: str,
        base_revision: int | None,
        detail: dict[str, Any],
    ) -> None:
        self.db.add(
            StudioDraftAuditEvent(
                tenant_id=self.tenant_id,
                draft_id=draft.id,
                event_type=event,
                revision=draft.revision,
                base_revision=base_revision,
                actor_user_id=self.actor_user_id,
                detail=_bounded_redacted(detail),
            )
        )

    async def read(self, draft_id: uuid.UUID) -> dict[str, Any]:
        draft = await self.db.scalar(
            select(StudioDraft).where(
                StudioDraft.id == draft_id, StudioDraft.tenant_id == self.tenant_id
            )
        )
        if draft is None:
            raise StudioError(404, "Template Studio draft not found")
        return await self._response(draft)

    async def create(
        self,
        request: StudioDraftCreate,
        idempotency_key: str,
        *,
        operation: str = "create",
        published_template_id: uuid.UUID | None = None,
        published_base_sha256: str | None = None,
    ) -> dict[str, Any]:
        reservation, replay = await self._idempotency(
            operation,
            idempotency_key,
            {
                "request": request,
                "published_template_id": published_template_id,
                "published_base_sha256": published_base_sha256,
            },
        )
        if replay is not None:
            return replay
        await self._tenant_admission_lock()
        await self._enforce_active_quota()
        artifact = await self.sources.resolve(request.source_artifact_id)
        if artifact.format != request.format:
            raise StudioError(
                422,
                {
                    "code": "source_format_mismatch",
                    "message": "source artifact format must match the draft format",
                },
            )
        await self.sources.read(
            artifact.id,
            expected_sha256=artifact.sha256,
            expected_media_type=artifact.media_type,
            expected_format=request.format,
        )
        draft = StudioDraft(
            tenant_id=self.tenant_id,
            published_template_id=published_template_id,
            published_base_sha256=published_base_sha256,
            source_artifact_id=artifact.id,
            source_sha256=artifact.sha256,
            source_media_type=artifact.media_type,
            title=request.title.strip(),
            format=request.format,
            identity_sha256="0" * 64,
            created_by_user_id=self.actor_user_id,
            updated_by_user_id=self.actor_user_id,
        )
        self.db.add(draft)
        await self.db.flush()
        field_ids: dict[str, uuid.UUID] = {}
        for field in request.fields:
            field_id = uuid.uuid4()
            field_ids[field.client_key] = field_id
            self.db.add(
                StudioDraftField(
                    id=field_id,
                    tenant_id=self.tenant_id,
                    draft_id=draft.id,
                    automation_key=field.automation_key,
                    label=field.label,
                    field_type=field.field_type,
                    required=field.required,
                    position=field.position,
                    definition=_field_definition(field.definition),
                )
            )
        for placement in request.placements:
            self.db.add(
                StudioDraftPlacement(
                    id=uuid.uuid4(),
                    tenant_id=self.tenant_id,
                    draft_id=draft.id,
                    field_id=field_ids[placement.field_client_key],
                    format=placement.format,
                    anchor_kind=placement.anchor_kind,
                    anchor=_bounded_redacted(
                        canonical_placement_anchor(
                            placement.format,
                            placement.anchor_kind,
                            placement.anchor,
                        )
                    ),
                )
            )
        await self.db.flush()
        fields, placements = await self._parts(draft.id)
        self._validate_parts(draft, fields, placements, raise_on_invalid=True)
        await self._refresh_identity(draft)
        self._audit(
            draft,
            "created",
            None,
            {
                "field_count": len(request.fields),
                "placement_count": len(request.placements),
            },
        )
        await self.db.flush()
        response = await self._response(draft)
        self._finish_idempotency(reservation, response)
        await self.db.commit()
        return response

    async def import_template(
        self, request: StudioDraftImport, idempotency_key: str
    ) -> dict[str, Any]:
        template = await self.db.scalar(
            select(DocumentTemplate).where(
                DocumentTemplate.id == request.template_id,
                DocumentTemplate.tenant_id == self.tenant_id,
            )
        )
        if template is None:
            raise StudioError(404, "Published template not found")
        source_bytes = await _verified_compatibility_source(template)
        template_format = template.format or "markdown"
        if template_format not in _CANONICAL_SOURCE_MEDIA:
            raise StudioError(422, {"code": "unsupported_source_format"})
        artifact = await self.sources.register(
            source_bytes,
            template_format,
            _CANONICAL_SOURCE_MEDIA[template_format],
        )
        raw_schema = template.variable_schema or {}
        if not isinstance(raw_schema, dict):
            raise StudioError(
                422,
                {
                    "code": "invalid_variable_schema",
                    "message": "published variable schema must be an object",
                },
            )
        raw_fields = raw_schema.get("fields") or []
        if not isinstance(raw_fields, list) or len(raw_fields) > 200:
            raise StudioError(
                422,
                {
                    "code": "invalid_variable_schema",
                    "message": "published variable schema is not safely importable",
                },
            )
        fields: list[dict[str, Any]] = []
        placements: list[dict[str, Any]] = []
        for position, raw in enumerate(raw_fields):
            if not isinstance(raw, dict):
                raise StudioError(
                    422,
                    {
                        "code": "invalid_variable_schema",
                        "message": "field entries must be objects",
                    },
                )
            key = str(raw.get("name") or raw.get("key") or "").strip()
            if not key:
                raise StudioError(
                    422,
                    {
                        "code": "invalid_variable_schema",
                        "message": "field key is required",
                    },
                )
            client_key = f"field-{position}"
            definition = {
                k: v for k, v in raw.items() if k in _ALLOWED_FIELD_DEFINITION_KEYS
            }
            fields.append(
                {
                    "client_key": client_key,
                    "automation_key": key,
                    "label": str(raw.get("label") or key)[:300],
                    "field_type": str(raw.get("type") or "text")[:40],
                    "required": bool(raw.get("required", False)),
                    "position": raw.get("position", position),
                    "definition": _field_definition(definition),
                }
            )
            canonical_items = raw.get("placements")
            if canonical_items is not None:
                if not isinstance(canonical_items, list) or len(canonical_items) > 20:
                    raise StudioError(
                        422,
                        {
                            "code": "invalid_variable_schema",
                            "message": "placement list is not safely importable",
                        },
                    )
                for canonical_item in canonical_items:
                    if not isinstance(canonical_item, dict):
                        raise StudioError(
                            422,
                            {
                                "code": "invalid_variable_schema",
                                "message": "placement entries must be objects",
                            },
                        )
                    unknown_placement_keys = set(canonical_item).difference(
                        {"studio_placement_id", "format", "anchor_kind", "anchor"}
                    )
                    if unknown_placement_keys:
                        raise StudioError(
                            422,
                            {
                                "code": "invalid_variable_schema",
                                "message": "placement entry contains unsupported metadata",
                            },
                        )
                    placements.append(
                        {
                            "client_key": f"placement-{len(placements)}",
                            "field_client_key": client_key,
                            "format": canonical_item.get("format"),
                            "anchor_kind": canonical_item.get("anchor_kind"),
                            "anchor": canonical_item.get("anchor"),
                        }
                    )
                if canonical_items:
                    continue
            if raw.get("pdf_field_name"):
                placements.append(
                    {
                        "client_key": f"placement-{len(placements)}",
                        "field_client_key": client_key,
                        "format": "pdf",
                        "anchor_kind": "acroform_field",
                        "anchor": {"field_name": str(raw["pdf_field_name"])[:500]},
                    }
                )
            overlay_items = raw.get("pdf_overlays") or (
                [raw["pdf_overlay"]] if isinstance(raw.get("pdf_overlay"), dict) else []
            )
            if not isinstance(overlay_items, list) or len(overlay_items) > 20:
                raise StudioError(
                    422,
                    {
                        "code": "invalid_variable_schema",
                        "message": "PDF overlay list is not safely importable",
                    },
                )
            for overlay in overlay_items[:20]:
                if not isinstance(overlay, dict):
                    raise StudioError(
                        422,
                        {
                            "code": "invalid_variable_schema",
                            "message": "PDF overlay entries must be objects",
                        },
                    )
                placements.append(
                    {
                        "client_key": f"placement-{len(placements)}",
                        "field_client_key": client_key,
                        "format": "pdf",
                        "anchor_kind": "overlay",
                        "anchor": overlay,
                    }
                )
            if isinstance(raw.get("docx_anchor"), dict):
                docx_anchor = dict(raw["docx_anchor"])
                if raw.get("docx_source_key"):
                    docx_anchor["source_key"] = raw["docx_source_key"]
                placements.append(
                    {
                        "client_key": f"placement-{len(placements)}",
                        "field_client_key": client_key,
                        "format": "docx",
                        "anchor_kind": "semantic_anchor",
                        "anchor": docx_anchor,
                    }
                )
            elif raw.get("docx_source_key"):
                placements.append(
                    {
                        "client_key": f"placement-{len(placements)}",
                        "field_client_key": client_key,
                        "format": "docx",
                        "anchor_kind": "source_key",
                        "anchor": {"source_key": str(raw["docx_source_key"])[:500]},
                    }
                )
            elif (template.format or "markdown") == "markdown":
                placements.append(
                    {
                        "client_key": f"placement-{len(placements)}",
                        "field_client_key": client_key,
                        "format": "markdown",
                        "anchor_kind": "template_token",
                        "anchor": {"token": key},
                    }
                )
        try:
            create_request = StudioDraftCreate.model_validate(
                {
                    "title": request.title or template.title,
                    "format": template_format,
                    "source_artifact_id": artifact.id,
                    "fields": fields,
                    "placements": placements,
                }
            )
        except ValidationError as exc:
            raise StudioError(
                422,
                {
                    "code": "invalid_variable_schema",
                    "message": "published variable schema is not safely importable",
                },
            ) from exc
        return await self.create(
            create_request,
            idempotency_key,
            operation="import_template",
            published_template_id=template.id,
            published_base_sha256=_published_template_base(template),
        )

    async def patch(
        self,
        draft_id: uuid.UUID,
        request: StudioDraftPatch,
        idempotency_key: str,
        *,
        operation: str = "patch",
    ) -> dict[str, Any]:
        reservation, replay = await self._idempotency(
            operation, idempotency_key, {"draft_id": draft_id, "request": request}
        )
        if replay is not None:
            return replay
        if any(item.op in {"archive", "restore"} for item in request.operations):
            await self._tenant_admission_lock()
        draft = await self._locked_draft(draft_id)
        self._check_revision(draft, request.base_revision)
        identity_changed = False
        invalidation_reason = None
        operation_names: list[str] = []

        for item in request.operations:
            operation_names.append(item.op)
            if item.op == "set_metadata":
                draft.title = item.title.strip()
            elif item.op == "upsert_field":
                payload = item.field
                row = None
                if payload.id is not None:
                    row = await self.db.scalar(
                        select(StudioDraftField).where(
                            StudioDraftField.id == payload.id,
                            StudioDraftField.tenant_id == self.tenant_id,
                            StudioDraftField.draft_id == draft.id,
                        )
                    )
                    if row is None:
                        raise StudioError(404, "Draft field not found")
                duplicate_key = await self.db.scalar(
                    select(StudioDraftField.id).where(
                        StudioDraftField.draft_id == draft.id,
                        StudioDraftField.tenant_id == self.tenant_id,
                        StudioDraftField.automation_key == payload.automation_key,
                        StudioDraftField.id != (payload.id or uuid.UUID(int=0)),
                    )
                )
                if duplicate_key is not None:
                    raise StudioError(
                        409,
                        {
                            "code": "automation_key_conflict",
                            "message": "automation key is already used in this draft",
                        },
                    )
                if row is None:
                    row = StudioDraftField(
                        id=uuid.uuid4(),
                        tenant_id=self.tenant_id,
                        draft_id=draft.id,
                        automation_key=payload.automation_key,
                        label=payload.label,
                        field_type=payload.field_type,
                        required=payload.required,
                        position=payload.position,
                        definition=_field_definition(payload.definition),
                    )
                    self.db.add(row)
                else:
                    row.automation_key = payload.automation_key
                    row.label = payload.label
                    row.field_type = payload.field_type
                    row.required = payload.required
                    row.position = payload.position
                    row.definition = _field_definition(payload.definition)
                identity_changed = True
                invalidation_reason = "field_contract_changed"
            elif item.op == "remove_field":
                result = await self.db.execute(
                    delete(StudioDraftField).where(
                        StudioDraftField.id == item.field_id,
                        StudioDraftField.draft_id == draft.id,
                        StudioDraftField.tenant_id == self.tenant_id,
                    )
                )
                if result.rowcount != 1:
                    raise StudioError(404, "Draft field not found")
                identity_changed = True
                invalidation_reason = "field_contract_changed"
            elif item.op == "upsert_placement":
                payload = item.placement
                field_exists = await self.db.scalar(
                    select(StudioDraftField.id).where(
                        StudioDraftField.id == payload.field_id,
                        StudioDraftField.draft_id == draft.id,
                        StudioDraftField.tenant_id == self.tenant_id,
                    )
                )
                if field_exists is None:
                    raise StudioError(404, "Draft field not found")
                if payload.format != draft.format:
                    raise StudioError(
                        422,
                        {
                            "code": "placement_format_mismatch",
                            "message": "placement format must match the draft format",
                        },
                    )
                row = None
                if payload.id is not None:
                    row = await self.db.scalar(
                        select(StudioDraftPlacement).where(
                            StudioDraftPlacement.id == payload.id,
                            StudioDraftPlacement.tenant_id == self.tenant_id,
                            StudioDraftPlacement.draft_id == draft.id,
                        )
                    )
                    if row is None:
                        raise StudioError(404, "Draft placement not found")
                anchor = _bounded_redacted(
                    canonical_placement_anchor(
                        payload.format, payload.anchor_kind, payload.anchor
                    )
                )
                if row is None:
                    row = StudioDraftPlacement(
                        id=uuid.uuid4(),
                        tenant_id=self.tenant_id,
                        draft_id=draft.id,
                        field_id=payload.field_id,
                        format=payload.format,
                        anchor_kind=payload.anchor_kind,
                        anchor=anchor,
                    )
                    self.db.add(row)
                else:
                    row.field_id = payload.field_id
                    row.format = payload.format
                    row.anchor_kind = payload.anchor_kind
                    row.anchor = anchor
                identity_changed = True
                invalidation_reason = "placement_contract_changed"
            elif item.op == "remove_placement":
                result = await self.db.execute(
                    delete(StudioDraftPlacement).where(
                        StudioDraftPlacement.id == item.placement_id,
                        StudioDraftPlacement.draft_id == draft.id,
                        StudioDraftPlacement.tenant_id == self.tenant_id,
                    )
                )
                if result.rowcount != 1:
                    raise StudioError(404, "Draft placement not found")
                identity_changed = True
                invalidation_reason = "placement_contract_changed"
            elif item.op == "replace_source":
                artifact = await self.sources.resolve(item.source_artifact_id)
                if artifact.format != draft.format:
                    raise StudioError(
                        422,
                        {
                            "code": "source_format_mismatch",
                            "message": "source artifact format must match the draft format",
                        },
                    )
                await self.sources.read(
                    artifact.id,
                    expected_sha256=artifact.sha256,
                    expected_media_type=artifact.media_type,
                    expected_format=draft.format,
                )
                draft.source_artifact_id = artifact.id
                draft.source_sha256 = artifact.sha256
                draft.source_media_type = artifact.media_type
                identity_changed = True
                invalidation_reason = "source_changed"
            elif item.op == "archive":
                draft.lifecycle_state = "archived"
                draft.archived_at = datetime.now(timezone.utc)
                invalidation_reason = "archived"
            elif item.op == "restore":
                if draft.lifecycle_state == "archived":
                    await self._enforce_active_quota()
                    draft.lifecycle_state = "active"
                    draft.archived_at = None
            elif item.op == "request_cancel":
                draft.cancellation_requested_at = datetime.now(timezone.utc)
                invalidation_reason = "cancellation_requested"
            elif item.op == "clear_cancel":
                draft.cancellation_requested_at = None

        draft.revision += 1
        draft.updated_by_user_id = self.actor_user_id
        draft.updated_at = datetime.now(timezone.utc)
        await self.db.flush()
        await self._validate_source_contract(draft)
        fields, placements = await self._parts(draft.id)
        self._validate_parts(draft, fields, placements, raise_on_invalid=True)
        if identity_changed:
            await self._refresh_identity(draft)
        invalidation_reason = invalidation_reason or "draft_revision_changed"
        draft.evidence_revision = None
        draft.evidence_invalidated_at = datetime.now(timezone.utc)
        draft.evidence_invalidation_reason = invalidation_reason
        self._audit(
            draft,
            "patched",
            request.base_revision,
            {
                "operations": operation_names,
                "identity_changed": identity_changed,
                "evidence_invalidation_reason": invalidation_reason,
            },
        )
        await self.db.flush()
        response = await self._response(draft)
        self._finish_idempotency(reservation, response)
        await self.db.commit()
        return response

    async def validate(
        self, draft_id: uuid.UUID, request: StudioRevisionRequest
    ) -> dict[str, Any]:
        draft = await self._locked_draft(draft_id)
        self._check_revision(draft, request.expected_revision)
        await self._validate_source_contract(draft)
        fields, placements = await self._parts(draft.id)
        issues = self._validate_parts(draft, fields, placements)
        if draft.lifecycle_state == "archived":
            issues.append({"code": "draft_archived"})
        return {
            "draft_id": draft.id,
            "revision": draft.revision,
            "identity_sha256": draft.identity_sha256,
            "valid": not issues,
            "issues": issues,
        }

    async def snapshot(
        self, draft_id: uuid.UUID, request: StudioRevisionRequest, idempotency_key: str
    ) -> dict[str, Any]:
        reservation, replay = await self._idempotency(
            "snapshot", idempotency_key, {"draft_id": draft_id, "request": request}
        )
        if replay is not None:
            return replay
        draft = await self._locked_draft(draft_id)
        self._check_revision(draft, request.expected_revision)
        await self._validate_source_contract(draft)
        existing = await self.db.scalar(
            select(StudioDraftSnapshot).where(
                StudioDraftSnapshot.tenant_id == self.tenant_id,
                StudioDraftSnapshot.draft_id == draft.id,
                StudioDraftSnapshot.revision == draft.revision,
            )
        )
        if existing is not None:
            response = {
                "id": existing.id,
                "draft_id": draft.id,
                "revision": existing.revision,
                "identity_sha256": existing.identity_sha256,
                "content_sha256": existing.content_sha256,
                "payload": existing.payload,
            }
            self._finish_idempotency(reservation, response)
            await self.db.commit()
            return response
        snapshot_count = await self.db.scalar(
            select(func.count())
            .select_from(StudioDraftSnapshot)
            .where(
                StudioDraftSnapshot.tenant_id == self.tenant_id,
                StudioDraftSnapshot.draft_id == draft.id,
            )
        )
        if (snapshot_count or 0) >= self.settings.TEMPLATE_STUDIO_SNAPSHOT_QUOTA:
            raise StudioError(
                429,
                {
                    "code": "snapshot_quota_exceeded",
                    "message": "draft snapshot quota reached",
                },
            )
        fields, placements = await self._parts(draft.id)
        self._validate_parts(draft, fields, placements, raise_on_invalid=True)
        payload = _bounded_redacted(
            {
                "contract_version": 1,
                "draft_id": str(draft.id),
                "revision": draft.revision,
                "identity_sha256": draft.identity_sha256,
                "format": draft.format,
                "lifecycle_state": draft.lifecycle_state,
                "source": source_contract(draft),
                "fields": self._identity_payload(draft, fields, placements)["fields"],
                "placements": self._identity_payload(draft, fields, placements)[
                    "placements"
                ],
            }
        )
        content_hash = _sha256(payload)
        snapshot = StudioDraftSnapshot(
            tenant_id=self.tenant_id,
            draft_id=draft.id,
            revision=draft.revision,
            identity_sha256=draft.identity_sha256,
            content_sha256=content_hash,
            payload=payload,
            created_by_user_id=self.actor_user_id,
        )
        self.db.add(snapshot)
        await self.db.flush()
        self._audit(
            draft,
            "snapshot_created",
            draft.revision,
            {
                "snapshot_id": str(snapshot.id),
                "content_sha256": content_hash,
            },
        )
        await self.db.flush()
        response = {
            "id": snapshot.id,
            "draft_id": draft.id,
            "revision": snapshot.revision,
            "identity_sha256": snapshot.identity_sha256,
            "content_sha256": snapshot.content_sha256,
            "payload": snapshot.payload,
        }
        self._finish_idempotency(reservation, response)
        await self.db.commit()
        return response

    async def read_snapshot(
        self, draft_id: uuid.UUID, snapshot_id: uuid.UUID
    ) -> dict[str, Any]:
        row = await self.db.scalar(
            select(StudioDraftSnapshot).where(
                StudioDraftSnapshot.id == snapshot_id,
                StudioDraftSnapshot.draft_id == draft_id,
                StudioDraftSnapshot.tenant_id == self.tenant_id,
            )
        )
        if row is None:
            raise StudioError(404, "Template Studio snapshot not found")
        return {
            "id": row.id,
            "draft_id": row.draft_id,
            "revision": row.revision,
            "identity_sha256": row.identity_sha256,
            "content_sha256": row.content_sha256,
            "payload": row.payload,
        }

    async def mark_render_evidence_if_current(
        self, draft_id: uuid.UUID, rendered_revision: int, rendered_identity_sha256: str
    ) -> bool:
        """Phase 3 completion gate: render output becomes evidence only here."""

        draft = await self._locked_draft(draft_id)
        current = (
            draft.lifecycle_state == "active"
            and draft.cancellation_requested_at is None
            and draft.revision == rendered_revision
            and draft.identity_sha256 == rendered_identity_sha256
        )
        if current:
            draft.evidence_revision = rendered_revision
            draft.evidence_invalidated_at = None
            draft.evidence_invalidation_reason = None
            await self.db.commit()
        else:
            await self.db.rollback()
        return current

    @staticmethod
    def variable_schema(
        fields: list[StudioDraftField], placements: list[StudioDraftPlacement]
    ) -> dict[str, Any]:
        by_field: dict[uuid.UUID, list[dict[str, Any]]] = {}
        legacy_by_field: dict[uuid.UUID, dict[str, Any]] = {}
        for item in placements:
            by_field.setdefault(item.field_id, []).append(
                {
                    "studio_placement_id": str(item.id),
                    "format": item.format,
                    "anchor_kind": item.anchor_kind,
                    "anchor": item.anchor,
                }
            )
            legacy = legacy_by_field.setdefault(item.field_id, {})
            if item.format == "pdf" and item.anchor_kind == "acroform_field":
                legacy.setdefault("pdf_field_name", item.anchor["field_name"])
            elif item.format == "pdf" and item.anchor_kind == "overlay":
                overlays = legacy.setdefault("pdf_overlays", [])
                overlays.append(item.anchor)
                legacy.setdefault("pdf_overlay", item.anchor)
            elif item.format == "docx" and item.anchor_kind == "source_key":
                legacy.setdefault("docx_source_key", item.anchor["source_key"])
            elif item.format == "docx" and item.anchor_kind == "semantic_anchor":
                anchor = dict(item.anchor)
                source_key = anchor.pop("source_key", None)
                legacy.setdefault("docx_anchor", anchor)
                if source_key:
                    legacy.setdefault("docx_source_key", source_key)
        return {
            "version": 2,
            "fields": [
                {
                    "studio_field_id": str(field.id),
                    "name": field.automation_key,
                    "label": field.label,
                    "type": field.field_type,
                    "required": field.required,
                    "position": field.position,
                    **field.definition,
                    **legacy_by_field.get(field.id, {}),
                    "placements": by_field.get(field.id, []),
                }
                for field in fields
            ],
        }

    async def promote(
        self, draft_id: uuid.UUID, request: StudioPromoteRequest, idempotency_key: str
    ) -> dict[str, Any]:
        reservation, replay = await self._idempotency(
            "promote", idempotency_key, {"draft_id": draft_id, "request": request}
        )
        if replay is not None:
            return replay
        draft = await self._locked_draft(draft_id)
        self._check_revision(draft, request.expected_revision)
        if (
            draft.lifecycle_state != "active"
            or draft.cancellation_requested_at is not None
        ):
            raise StudioError(
                409,
                {
                    "code": "draft_not_publishable",
                    "message": "archived or cancelled drafts cannot be promoted",
                },
            )
        if draft.published_template_id is None:
            raise StudioError(
                422,
                {
                    "code": "materialization_required",
                    "message": "new-template materialization is owned by a later publish phase",
                },
            )
        template = await self.db.scalar(
            select(DocumentTemplate)
            .where(
                DocumentTemplate.id == draft.published_template_id,
                DocumentTemplate.tenant_id == self.tenant_id,
            )
            .with_for_update()
        )
        if template is None:
            raise StudioError(404, "Published template not found")
        current_base = _published_template_base(template)
        if (
            draft.published_base_sha256 is None
            or draft.published_base_sha256 != current_base
        ):
            raise StudioError(
                409,
                {
                    "code": "stale_published_template",
                    "message": "published compatibility record changed since import",
                },
            )
        current_source = await _verified_compatibility_source(template)
        current_source, _ = _canonical_source_bytes(
            current_source,
            draft.format,
            _CANONICAL_SOURCE_MEDIA[draft.format],
        )
        current_source_hash = hashlib.sha256(current_source).hexdigest()
        if current_source_hash != draft.source_sha256:
            raise StudioError(
                409,
                {
                    "code": "source_hash_mismatch",
                    "message": "published source changed outside this draft",
                },
            )
        await self.sources.read(
            draft.source_artifact_id,
            expected_sha256=draft.source_sha256,
            expected_media_type=draft.source_media_type,
            expected_format=draft.format,
        )
        fields, placements = await self._parts(draft.id)
        self._validate_parts(draft, fields, placements, raise_on_invalid=True)
        template.title = draft.title
        template.variable_schema = _bounded_redacted(
            self.variable_schema(fields, placements)
        )
        template.status = request.status
        template.updated_at = datetime.now(timezone.utc)
        draft.published_base_sha256 = _published_template_base(template)
        base = draft.revision
        draft.revision += 1
        draft.updated_by_user_id = self.actor_user_id
        draft.updated_at = datetime.now(timezone.utc)
        draft.evidence_revision = None
        draft.evidence_invalidated_at = datetime.now(timezone.utc)
        draft.evidence_invalidation_reason = "published_compatibility_updated"
        self._audit(
            draft,
            "promoted",
            base,
            {
                "template_id": str(template.id),
                "published_status": request.status,
            },
        )
        await self.db.flush()
        response = await self._response(draft)
        self._finish_idempotency(reservation, response)
        await self.db.commit()
        return response

    async def purge_expired_idempotency(self) -> int:
        result = await self.db.execute(
            delete(StudioDraftIdempotency).where(
                StudioDraftIdempotency.tenant_id == self.tenant_id,
                StudioDraftIdempotency.expires_at < datetime.now(timezone.utc),
            )
        )
        return result.rowcount or 0


class StudioProposalBoundary:
    """Phase 4 extension seam; revision 147 intentionally stores no proposals.

    Implementations must redact proposal payloads, retain idempotency keys for a
    configured period, use the bounded operation vocabulary from the API schema,
    and call ``StudioDraftService.patch(..., operation='accept_proposal')`` so
    acceptance is atomic and advances the draft revision exactly once.
    """

    async def create_proposal(self, *args, **kwargs):  # pragma: no cover - interface
        raise NotImplementedError

    async def get_proposal(self, *args, **kwargs):  # pragma: no cover - interface
        raise NotImplementedError

    async def accept_proposal(self, *args, **kwargs):  # pragma: no cover - interface
        raise NotImplementedError

    async def launch_test_render(self, *args, **kwargs):  # pragma: no cover - interface
        raise NotImplementedError
