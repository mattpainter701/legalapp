"""Authoritative Template Studio draft transaction and redaction boundary."""

from __future__ import annotations

import hashlib
import json
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
)

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
    }


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

    async def _ensure_source_artifact(
        self, artifact_id: uuid.UUID, sha256: str, media_type: str
    ) -> None:
        artifact = await self.db.scalar(
            select(StudioSourceArtifact).where(
                StudioSourceArtifact.id == artifact_id,
                StudioSourceArtifact.tenant_id == self.tenant_id,
            )
        )
        if artifact is None:
            self.db.add(
                StudioSourceArtifact(
                    id=artifact_id,
                    tenant_id=self.tenant_id,
                    sha256=sha256,
                    media_type=media_type,
                    created_by_user_id=self.actor_user_id,
                )
            )
            await self.db.flush()
            return
        if artifact.sha256 != sha256 or artifact.media_type != media_type:
            raise StudioError(
                409,
                {
                    "code": "source_hash_mismatch",
                    "message": "an immutable artifact ID cannot be rebound to different bytes",
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
    ) -> dict[str, Any]:
        reservation, replay = await self._idempotency(
            operation, idempotency_key, request
        )
        if replay is not None:
            return replay
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

        if request.published_template_id is not None:
            template = await self.db.scalar(
                select(DocumentTemplate).where(
                    DocumentTemplate.id == request.published_template_id,
                    DocumentTemplate.tenant_id == self.tenant_id,
                )
            )
            if template is None:
                raise StudioError(404, "Published template not found")
            template_hash = (
                template.source_sha256
                or hashlib.sha256(template.body.encode("utf-8")).hexdigest()
            )
            if template_hash != request.source_sha256:
                raise StudioError(
                    409,
                    {
                        "code": "source_hash_mismatch",
                        "message": "draft source does not match the published compatibility record",
                    },
                )

        artifact_id = request.source_artifact_id or uuid.uuid4()
        await self._ensure_source_artifact(
            artifact_id, request.source_sha256, request.source_media_type
        )
        draft = StudioDraft(
            tenant_id=self.tenant_id,
            published_template_id=request.published_template_id,
            source_artifact_id=artifact_id,
            source_sha256=request.source_sha256,
            source_media_type=request.source_media_type,
            title=request.title.strip(),
            format=request.format,
            identity_sha256="0" * 64,
            created_by_user_id=self.actor_user_id,
            updated_by_user_id=self.actor_user_id,
        )
        self.db.add(draft)
        await self.db.flush()
        for field in request.fields:
            self.db.add(
                StudioDraftField(
                    id=field.id or uuid.uuid4(),
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
                    id=placement.id or uuid.uuid4(),
                    tenant_id=self.tenant_id,
                    draft_id=draft.id,
                    field_id=placement.field_id,
                    format=placement.format,
                    anchor_kind=placement.anchor_kind,
                    anchor=_bounded_redacted(placement.anchor),
                )
            )
        await self.db.flush()
        await self._refresh_identity(draft)
        self._audit(
            draft,
            "created",
            None,
            {
                "field_count": len(request.fields),
                "placement_count": len(request.placements),
                "source_artifact_id": str(draft.source_artifact_id),
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
        source_sha = (
            template.source_sha256
            or hashlib.sha256(template.body.encode("utf-8")).hexdigest()
        )
        raw_fields = (template.variable_schema or {}).get("fields") or []
        if not isinstance(raw_fields, list) or len(raw_fields) > 200:
            raise StudioError(
                422,
                {
                    "code": "invalid_variable_schema",
                    "message": "published variable schema is not safely importable",
                },
            )
        fields = []
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
            try:
                field_id = (
                    uuid.UUID(str(raw["studio_field_id"]))
                    if raw.get("studio_field_id")
                    else uuid.uuid4()
                )
            except (TypeError, ValueError) as exc:
                raise StudioError(
                    422,
                    {
                        "code": "invalid_variable_schema",
                        "message": "studio_field_id must be a UUID",
                    },
                ) from exc
            definition = {
                k: v for k, v in raw.items() if k in _ALLOWED_FIELD_DEFINITION_KEYS
            }
            fields.append(
                {
                    "id": field_id,
                    "automation_key": key,
                    "label": str(raw.get("label") or key)[:300],
                    "field_type": str(raw.get("type") or "text")[:40],
                    "required": bool(raw.get("required", False)),
                    "position": int(raw.get("position", position)),
                    "definition": _field_definition(definition),
                }
            )
            if raw.get("pdf_field_name"):
                placements.append(
                    {
                        "id": uuid.uuid4(),
                        "field_id": field_id,
                        "format": "pdf",
                        "anchor_kind": "acroform_field",
                        "anchor": {"field_name": str(raw["pdf_field_name"])[:500]},
                    }
                )
            overlay_items = raw.get("pdf_overlays") or (
                [raw["pdf_overlay"]] if isinstance(raw.get("pdf_overlay"), dict) else []
            )
            for overlay in overlay_items[:20]:
                if isinstance(overlay, dict):
                    clean_overlay = {
                        k: v
                        for k, v in overlay.items()
                        if k not in {"source_text", "text", "value", "default"}
                    }
                    placements.append(
                        {
                            "id": uuid.uuid4(),
                            "field_id": field_id,
                            "format": "pdf",
                            "anchor_kind": "overlay",
                            "anchor": _bounded_redacted(clean_overlay),
                        }
                    )
            if isinstance(raw.get("docx_anchor"), dict):
                clean_anchor = {
                    k: v
                    for k, v in raw["docx_anchor"].items()
                    if k not in {"source_text", "text", "value", "default"}
                }
                placements.append(
                    {
                        "id": uuid.uuid4(),
                        "field_id": field_id,
                        "format": "docx",
                        "anchor_kind": "docx_anchor",
                        "anchor": _bounded_redacted(clean_anchor),
                    }
                )
            elif raw.get("docx_source_key"):
                placements.append(
                    {
                        "id": uuid.uuid4(),
                        "field_id": field_id,
                        "format": "docx",
                        "anchor_kind": "source_key",
                        "anchor": {"source_key": str(raw["docx_source_key"])[:500]},
                    }
                )
            elif (template.format or "markdown") == "markdown":
                placements.append(
                    {
                        "id": uuid.uuid4(),
                        "field_id": field_id,
                        "format": "markdown",
                        "anchor_kind": "template_token",
                        "anchor": {"token": key},
                    }
                )
        try:
            create_request = StudioDraftCreate.model_validate(
                {
                    "title": request.title or template.title,
                    "format": template.format or "markdown",
                    "source_artifact_id": uuid.uuid4(),
                    "source_sha256": source_sha,
                    "source_media_type": template.source_content_type
                    or "text/markdown",
                    "published_template_id": template.id,
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
            create_request, idempotency_key, operation="import_template"
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
                    scoped_row = await self.db.scalar(
                        select(StudioDraftField).where(
                            StudioDraftField.id == payload.id,
                            StudioDraftField.tenant_id == self.tenant_id,
                        )
                    )
                    if scoped_row is not None and scoped_row.draft_id != draft.id:
                        raise StudioError(
                            409,
                            {
                                "code": "field_scope_mismatch",
                                "message": "field UUID already belongs to another draft",
                            },
                        )
                    row = scoped_row
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
                        id=payload.id or uuid.uuid4(),
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
                    raise StudioError(
                        422,
                        {
                            "code": "unknown_field",
                            "message": "placement field is not in this draft",
                        },
                    )
                row = None
                if payload.id is not None:
                    scoped_row = await self.db.scalar(
                        select(StudioDraftPlacement).where(
                            StudioDraftPlacement.id == payload.id,
                            StudioDraftPlacement.tenant_id == self.tenant_id,
                        )
                    )
                    if scoped_row is not None and scoped_row.draft_id != draft.id:
                        raise StudioError(
                            409,
                            {
                                "code": "placement_scope_mismatch",
                                "message": "placement UUID already belongs to another draft",
                            },
                        )
                    row = scoped_row
                if row is None:
                    row = StudioDraftPlacement(
                        id=payload.id or uuid.uuid4(),
                        tenant_id=self.tenant_id,
                        draft_id=draft.id,
                        field_id=payload.field_id,
                        format=payload.format,
                        anchor_kind=payload.anchor_kind,
                        anchor=_bounded_redacted(payload.anchor),
                    )
                    self.db.add(row)
                else:
                    row.field_id = payload.field_id
                    row.format = payload.format
                    row.anchor_kind = payload.anchor_kind
                    row.anchor = _bounded_redacted(payload.anchor)
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
                await self._ensure_source_artifact(
                    item.source_artifact_id, item.source_sha256, item.source_media_type
                )
                draft.source_artifact_id = item.source_artifact_id
                draft.source_sha256 = item.source_sha256
                draft.source_media_type = item.source_media_type
                identity_changed = True
                invalidation_reason = "source_changed"
            elif item.op == "archive":
                draft.lifecycle_state = "archived"
                draft.archived_at = datetime.now(timezone.utc)
                invalidation_reason = "archived"
            elif item.op == "restore":
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
        fields, placements = await self._parts(draft.id)
        issues: list[dict[str, str]] = []
        keys: set[str] = set()
        for field in fields:
            if field.automation_key in keys:
                issues.append(
                    {"code": "duplicate_automation_key", "field_id": str(field.id)}
                )
            keys.add(field.automation_key)
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
        payload = _bounded_redacted(
            {
                "contract_version": 1,
                "draft_id": str(draft.id),
                "revision": draft.revision,
                "identity_sha256": draft.identity_sha256,
                "title": draft.title,
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
        return current

    @staticmethod
    def variable_schema(
        fields: list[StudioDraftField], placements: list[StudioDraftPlacement]
    ) -> dict[str, Any]:
        by_field: dict[uuid.UUID, list[dict[str, Any]]] = {}
        for item in placements:
            by_field.setdefault(item.field_id, []).append(
                {
                    "studio_placement_id": str(item.id),
                    "format": item.format,
                    "anchor_kind": item.anchor_kind,
                    "anchor": item.anchor,
                }
            )
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
        current_source_hash = (
            template.source_sha256
            or hashlib.sha256(template.body.encode("utf-8")).hexdigest()
        )
        if current_source_hash != draft.source_sha256:
            raise StudioError(
                409,
                {
                    "code": "source_hash_mismatch",
                    "message": "published source changed outside this draft",
                },
            )
        fields, placements = await self._parts(draft.id)
        template.title = draft.title
        template.variable_schema = _bounded_redacted(
            self.variable_schema(fields, placements)
        )
        template.status = request.status
        template.updated_at = datetime.now(timezone.utc)
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
