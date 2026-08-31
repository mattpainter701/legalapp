"""Agreement-gate, data-inventory, and retention-enforcement services."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import blake2b
from pathlib import Path
from typing import Any, Literal, TypedDict

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import set_tenant_context
from app.models.compliance import (
    AgreementDefinition,
    RetentionAction,
    RetentionPolicy,
    TenantAgreementAcceptance,
)
from app.models.conversion_loop import LeadChannelConsent, SmsConsentEvent
from app.models.conversation import Conversation, Message
from app.models.document import Document
from app.models.document_template import DocumentTemplate
from app.models.inbound_email import InboundEmail
from app.models.matter_document import MatterDocument
from app.models.sms import (
    SmsMessage,
    SmsNumberSuppression,
    SmsNumberSuppressionEvent,
    SmsProviderConfig,
    SmsReviewItem,
)
from app.models.tenant import Tenant

settings = get_settings()

CHAT_ATTACHMENTS_POLICY_KEY = "chat_attachments_days"
MIN_CHAT_ATTACHMENT_DAYS = 1
MAX_CHAT_ATTACHMENT_DAYS = 365

SmsDataClassification = Literal[
    "customer_communication_content",
    "compliance_suppression_state",
    "current_consent_state",
    "immutable_consent_evidence",
    "immutable_stop_start_evidence",
    "operational_review_evidence",
    "security_configuration_metadata",
]
SmsRetentionMode = Literal[
    "firm_records_policy_with_reconciliation_evidence",
    "compliance_suppression_record",
    "current_consent_record",
    "consent_evidence",
    "stop_start_evidence",
    "review_evidence",
    "security_configuration_metadata",
]
SmsExportBoundary = Literal[
    "customer_export_includes_content_and_delivery_state",
    "customer_export_includes_current_suppression_state",
    "customer_export_includes_current_consent_state",
    "immutable_evidence_summary_only",
    "security_metadata_only_no_secret_values",
]


class SmsRetentionCategory(TypedDict):
    name: str
    system: Literal["postgres"]
    record_count: int
    oldest_at: datetime | None
    bytes: None
    retention_mode: SmsRetentionMode
    deletion_supported: Literal[False]
    data_classification: SmsDataClassification
    export_boundary: SmsExportBoundary
    legal_hold_behavior: Literal["preserve_when_active"]


@dataclass(frozen=True)
class _SmsRetentionSpec:
    name: str
    data_classification: SmsDataClassification
    retention_mode: SmsRetentionMode
    export_boundary: SmsExportBoundary


_SMS_RETENTION_SPECS: dict[str, _SmsRetentionSpec] = {
    "sms_messages": _SmsRetentionSpec(
        name="sms_content_and_delivery",
        data_classification="customer_communication_content",
        retention_mode="firm_records_policy_with_reconciliation_evidence",
        export_boundary="customer_export_includes_content_and_delivery_state",
    ),
    "sms_number_suppressions": _SmsRetentionSpec(
        name="sms_suppressions",
        data_classification="compliance_suppression_state",
        retention_mode="compliance_suppression_record",
        export_boundary="customer_export_includes_current_suppression_state",
    ),
    "lead_channel_consents": _SmsRetentionSpec(
        name="sms_current_consent_state",
        data_classification="current_consent_state",
        retention_mode="current_consent_record",
        export_boundary="customer_export_includes_current_consent_state",
    ),
    "sms_consent_events": _SmsRetentionSpec(
        name="sms_consent_evidence",
        data_classification="immutable_consent_evidence",
        retention_mode="consent_evidence",
        export_boundary="immutable_evidence_summary_only",
    ),
    "sms_number_suppression_events": _SmsRetentionSpec(
        name="sms_number_evidence",
        data_classification="immutable_stop_start_evidence",
        retention_mode="stop_start_evidence",
        export_boundary="immutable_evidence_summary_only",
    ),
    "sms_review_items": _SmsRetentionSpec(
        name="sms_review_evidence",
        data_classification="operational_review_evidence",
        retention_mode="review_evidence",
        export_boundary="immutable_evidence_summary_only",
    ),
    "sms_provider_configs": _SmsRetentionSpec(
        name="sms_provider_configuration",
        data_classification="security_configuration_metadata",
        retention_mode="security_configuration_metadata",
        export_boundary="security_metadata_only_no_secret_values",
    ),
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def current_agreement_definitions(
    db: AsyncSession, *, now: datetime | None = None
) -> list[AgreementDefinition]:
    """Return the latest effective definition for every agreement kind."""

    effective_now = now or _utcnow()
    rows = list(
        (
            await db.scalars(
                select(AgreementDefinition)
                .where(
                    AgreementDefinition.effective_at <= effective_now,
                    or_(
                        AgreementDefinition.expires_at.is_(None),
                        AgreementDefinition.expires_at > effective_now,
                    ),
                )
                .order_by(
                    AgreementDefinition.kind,
                    AgreementDefinition.effective_at.desc(),
                    AgreementDefinition.created_at.desc(),
                )
            )
        ).all()
    )
    current: dict[str, AgreementDefinition] = {}
    for row in rows:
        current.setdefault(row.kind, row)
    return list(current.values())


async def agreement_status(db: AsyncSession, tenant_id: uuid.UUID) -> dict[str, Any]:
    definitions = await current_agreement_definitions(db)
    accepted = {
        row.agreement_definition_id: row
        for row in (
            await db.scalars(
                select(TenantAgreementAcceptance).where(
                    TenantAgreementAcceptance.tenant_id == tenant_id
                )
            )
        ).all()
    }

    items: list[dict[str, Any]] = []
    for definition in definitions:
        acceptance = accepted.get(definition.id)
        is_current = bool(
            acceptance
            and acceptance.status == "accepted"
            and acceptance.document_hash == definition.content_hash
            and acceptance.document_version == definition.version
            and acceptance.authority_attested
        )
        items.append(
            {
                "id": str(definition.id),
                "kind": definition.kind,
                "version": definition.version,
                "title": definition.title,
                "document_url": definition.document_url,
                "content_hash": definition.content_hash,
                "counsel_owned": definition.counsel_owned,
                "required": definition.required_for_onboarding,
                "effective_at": definition.effective_at,
                "expires_at": definition.expires_at,
                "accepted": is_current,
                "accepted_at": acceptance.accepted_at if acceptance else None,
                "signer_name": acceptance.signer_name if acceptance else None,
                "signer_title": acceptance.signer_title if acceptance else None,
                "signer_email": acceptance.signer_email if acceptance else None,
                "authority_attested": bool(
                    acceptance and acceptance.authority_attested
                ),
                "esign_provider": acceptance.esign_provider if acceptance else None,
                "esign_envelope_id": (
                    acceptance.esign_envelope_id if acceptance else None
                ),
                "evidence_reference": (
                    acceptance.evidence_reference if acceptance else None
                ),
            }
        )

    required = [item for item in items if item["required"]]
    configured = bool(required)
    complete = configured and all(item["accepted"] for item in required)
    enforced = bool(settings.TENANT_AGREEMENT_GATE_ENABLED)
    return {
        "configured": configured,
        "complete": complete,
        "enforced": enforced,
        "blocking": enforced and not complete,
        "agreements": items,
    }


async def chat_attachment_ttl_days(db: AsyncSession, tenant_id: uuid.UUID) -> int:
    policy = await db.scalar(
        select(RetentionPolicy).where(RetentionPolicy.tenant_id == tenant_id)
    )
    configured = (
        (policy.policy_json or {}).get(CHAT_ATTACHMENTS_POLICY_KEY) if policy else None
    )
    if configured is None:
        return int(settings.CHAT_ATTACHMENT_TTL_DAYS)
    return max(
        MIN_CHAT_ATTACHMENT_DAYS,
        min(MAX_CHAT_ATTACHMENT_DAYS, int(configured)),
    )


async def _aggregate(
    db: AsyncSession,
    *,
    id_column,
    created_column,
    where: list[Any],
    bytes_column=None,
) -> dict[str, Any]:
    selected = [func.count(id_column), func.min(created_column)]
    if bytes_column is not None:
        selected.append(func.coalesce(func.sum(bytes_column), 0))
    row = (await db.execute(select(*selected).where(*where))).one()
    return {
        "record_count": int(row[0] or 0),
        "oldest_at": row[1],
        "bytes": int(row[2] or 0) if bytes_column is not None else None,
    }


def _sms_retention_category(
    table_name: str, aggregate: dict[str, Any]
) -> SmsRetentionCategory:
    """Build an explicit, metadata-only SMS retention classification."""

    spec = _SMS_RETENTION_SPECS.get(table_name)
    if spec is None:
        raise ValueError(f"SMS retention store is unclassified: {table_name}")
    if set(aggregate) != {"record_count", "oldest_at", "bytes"}:
        raise ValueError(f"SMS retention aggregate is malformed: {table_name}")
    record_count = aggregate["record_count"]
    oldest_at = aggregate["oldest_at"]
    if (
        not isinstance(record_count, int)
        or isinstance(record_count, bool)
        or record_count < 0
        or (oldest_at is not None and not isinstance(oldest_at, datetime))
        or aggregate["bytes"] is not None
    ):
        raise ValueError(f"SMS retention aggregate is malformed: {table_name}")
    return {
        "name": spec.name,
        "system": "postgres",
        "record_count": record_count,
        "oldest_at": oldest_at,
        "bytes": None,
        "retention_mode": spec.retention_mode,
        "deletion_supported": False,
        "data_classification": spec.data_classification,
        "export_boundary": spec.export_boundary,
        "legal_hold_behavior": "preserve_when_active",
    }


async def retention_inventory(db: AsyncSession, tenant_id: uuid.UUID) -> dict[str, Any]:
    """Return metadata-only counts for each customer-data store in this app."""

    policy = await db.scalar(
        select(RetentionPolicy).where(RetentionPolicy.tenant_id == tenant_id)
    )
    configured_days = await chat_attachment_ttl_days(db, tenant_id)

    document_index = await _aggregate(
        db,
        id_column=Document.id,
        created_column=Document.created_at,
        bytes_column=Document.file_size,
        where=[Document.tenant_id == tenant_id],
    )
    chat_attachments = await _aggregate(
        db,
        id_column=Document.id,
        created_column=Document.created_at,
        bytes_column=Document.file_size,
        where=[
            Document.tenant_id == tenant_id,
            Document.conversation_id.is_not(None),
            Document.matter_id.is_(None),
        ],
    )
    local_references = await _aggregate(
        db,
        id_column=Document.id,
        created_column=Document.created_at,
        bytes_column=Document.file_size,
        where=[
            Document.tenant_id == tenant_id,
            Document.storage_path.is_not(None),
            ~Document.storage_path.ilike("http%"),
        ],
    )
    matter_files = await _aggregate(
        db,
        id_column=MatterDocument.id,
        created_column=MatterDocument.created_at,
        bytes_column=MatterDocument.file_size,
        where=[MatterDocument.tenant_id == tenant_id],
    )
    conversations = await _aggregate(
        db,
        id_column=Conversation.id,
        created_column=Conversation.created_at,
        where=[Conversation.tenant_id == tenant_id],
    )
    messages = await _aggregate(
        db,
        id_column=Message.id,
        created_column=Message.created_at,
        where=[Message.tenant_id == tenant_id],
    )
    templates = await _aggregate(
        db,
        id_column=DocumentTemplate.id,
        created_column=DocumentTemplate.created_at,
        bytes_column=DocumentTemplate.source_file_size,
        where=[DocumentTemplate.tenant_id == tenant_id],
    )
    inbound = await _aggregate(
        db,
        id_column=InboundEmail.id,
        created_column=InboundEmail.created_at,
        bytes_column=InboundEmail.raw_size,
        where=[InboundEmail.tenant_id == tenant_id],
    )
    agreement_evidence = await _aggregate(
        db,
        id_column=TenantAgreementAcceptance.id,
        created_column=TenantAgreementAcceptance.accepted_at,
        where=[TenantAgreementAcceptance.tenant_id == tenant_id],
    )
    sms_content = await _aggregate(
        db,
        id_column=SmsMessage.id,
        created_column=SmsMessage.created_at,
        where=[SmsMessage.tenant_id == tenant_id],
    )
    sms_suppressions = await _aggregate(
        db,
        id_column=SmsNumberSuppression.id,
        created_column=SmsNumberSuppression.created_at,
        where=[SmsNumberSuppression.tenant_id == tenant_id],
    )
    sms_consent_evidence = await _aggregate(
        db,
        id_column=SmsConsentEvent.id,
        created_column=SmsConsentEvent.occurred_at,
        where=[SmsConsentEvent.tenant_id == tenant_id],
    )
    sms_current_consent_state = await _aggregate(
        db,
        id_column=LeadChannelConsent.id,
        created_column=LeadChannelConsent.updated_at,
        where=[LeadChannelConsent.tenant_id == tenant_id],
    )
    sms_number_evidence = await _aggregate(
        db,
        id_column=SmsNumberSuppressionEvent.id,
        created_column=SmsNumberSuppressionEvent.occurred_at,
        where=[SmsNumberSuppressionEvent.tenant_id == tenant_id],
    )
    sms_review_evidence = await _aggregate(
        db,
        id_column=SmsReviewItem.id,
        created_column=SmsReviewItem.created_at,
        where=[SmsReviewItem.tenant_id == tenant_id],
    )
    sms_provider_configuration = await _aggregate(
        db,
        id_column=SmsProviderConfig.id,
        created_column=SmsProviderConfig.created_at,
        where=[SmsProviderConfig.tenant_id == tenant_id],
    )

    provider_rows = (
        await db.execute(
            select(
                MatterDocument.storage_provider,
                func.count(MatterDocument.id),
                func.coalesce(func.sum(MatterDocument.file_size), 0),
            )
            .where(MatterDocument.tenant_id == tenant_id)
            .group_by(MatterDocument.storage_provider)
            .order_by(MatterDocument.storage_provider)
        )
    ).all()

    categories = [
        {
            "name": "matter_files",
            "system": "customer_cloud_and_control_plane",
            **matter_files,
            "retention_mode": "firm_records_policy",
            "deletion_supported": False,
        },
        {
            "name": "document_index",
            "system": "postgres_and_upload_bind",
            **document_index,
            "retention_mode": "source_record_or_explicit_expiry",
            "deletion_supported": False,
        },
        {
            "name": "local_file_references",
            "system": "uploads_bind",
            **local_references,
            "retention_mode": "inventory_only",
            "deletion_supported": False,
        },
        {
            "name": "chat_attachments",
            "system": "postgres_and_upload_bind",
            **chat_attachments,
            "retention_mode": f"rolling_{configured_days}_days",
            "deletion_supported": True,
        },
        {
            "name": "conversations",
            "system": "postgres",
            **conversations,
            "retention_mode": "firm_records_policy",
            "deletion_supported": False,
        },
        {
            "name": "messages",
            "system": "postgres",
            **messages,
            "retention_mode": "firm_records_policy",
            "deletion_supported": False,
        },
        {
            "name": "document_templates",
            "system": "postgres_and_customer_cloud_or_upload_bind",
            **templates,
            "retention_mode": "explicit_admin_action",
            "deletion_supported": False,
        },
        {
            "name": "inbound_email_queue",
            "system": "postgres_and_upload_bind",
            **inbound,
            "retention_mode": "matter_review_required",
            "deletion_supported": False,
        },
        {
            "name": "agreement_evidence",
            "system": "postgres",
            **agreement_evidence,
            "retention_mode": "contract_evidence",
            "deletion_supported": False,
        },
        _sms_retention_category("sms_messages", sms_content),
        _sms_retention_category("sms_number_suppressions", sms_suppressions),
        _sms_retention_category("lead_channel_consents", sms_current_consent_state),
        _sms_retention_category("sms_consent_events", sms_consent_evidence),
        _sms_retention_category("sms_number_suppression_events", sms_number_evidence),
        _sms_retention_category("sms_review_items", sms_review_evidence),
        _sms_retention_category("sms_provider_configs", sms_provider_configuration),
    ]

    actions = list(
        (
            await db.scalars(
                select(RetentionAction)
                .where(RetentionAction.tenant_id == tenant_id)
                .order_by(RetentionAction.created_at.desc())
                .limit(20)
            )
        ).all()
    )
    return {
        "tenant_id": str(tenant_id),
        "legal_hold": bool(policy and policy.legal_hold),
        "legal_hold_reason": policy.legal_hold_reason if policy else None,
        "legal_hold_set_at": policy.legal_hold_set_at if policy else None,
        "policy": {
            CHAT_ATTACHMENTS_POLICY_KEY: configured_days,
        },
        "policy_version": policy.version if policy else 0,
        "categories": categories,
        "matter_file_providers": [
            {
                "provider": str(provider or "unknown"),
                "record_count": int(count or 0),
                "bytes": int(size or 0),
            }
            for provider, count, size in provider_rows
        ],
        "recent_actions": [
            {
                "id": str(action.id),
                "action": action.action,
                "status": action.status,
                "dry_run": action.dry_run,
                "actor_type": action.actor_type,
                "legal_hold_at_execution": action.legal_hold_at_execution,
                "policy_version": action.policy_version,
                "result": action.result_json,
                "created_at": action.created_at,
            }
            for action in actions
        ],
    }


async def lock_tenant_for_retention(db: AsyncSession, tenant_id: uuid.UUID) -> None:
    # Serialize retention policy changes and cleanup for one tenant without
    # locking the tenant row itself. Other workflows legitimately update that
    # row (for example, advancing the RAG corpus revision) while cleanup must
    # still be able to reach Document's SKIP LOCKED boundary.
    lock_key = int.from_bytes(
        blake2b(f"retention:{tenant_id}".encode(), digest_size=8).digest(),
        byteorder="big",
        signed=True,
    )
    await db.execute(select(func.pg_advisory_xact_lock(lock_key)))
    exists = await db.scalar(select(Tenant.id).where(Tenant.id == tenant_id))
    if not exists:
        raise LookupError("Tenant not found")


async def reschedule_chat_attachments(
    db: AsyncSession, tenant_id: uuid.UUID, *, days: int
) -> int:
    rows = list(
        (
            await db.scalars(
                select(Document)
                .where(
                    Document.tenant_id == tenant_id,
                    Document.conversation_id.is_not(None),
                    Document.matter_id.is_(None),
                    Document.expires_at.is_not(None),
                )
                .with_for_update()
            )
        ).all()
    )
    for row in rows:
        row.expires_at = row.created_at + timedelta(days=days)
    return len(rows)


def _delete_local_file(tenant_id: uuid.UUID, raw_path: str | None) -> str:
    if not raw_path:
        return "missing_reference"
    if raw_path.lower().startswith(("http://", "https://")):
        return "external_reference"

    tenant_root = (Path(settings.UPLOAD_DIR) / str(tenant_id)).resolve()
    unresolved = Path(raw_path)
    if unresolved.is_symlink():
        raise ValueError("retention path must not be a symlink")
    path = unresolved.resolve()
    try:
        path.relative_to(tenant_root)
    except ValueError as exc:
        raise ValueError("retention path is outside the tenant upload root") from exc

    if not path.exists():
        return "already_missing"
    if not path.is_file():
        raise ValueError("retention path is not a regular file")
    path.unlink()

    parent = path.parent
    while parent != tenant_root:
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent
    return "deleted"


async def execute_chat_attachment_retention(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    dry_run: bool,
    actor_user_id: uuid.UUID | None,
    actor_type: str,
) -> dict[str, Any]:
    """Preview or delete only already-expired, non-matter chat attachments."""

    await lock_tenant_for_retention(db, tenant_id)
    policy = await db.scalar(
        select(RetentionPolicy)
        .where(RetentionPolicy.tenant_id == tenant_id)
        .with_for_update()
    )
    legal_hold = bool(policy and policy.legal_hold)
    now = _utcnow()
    query = select(Document).where(
        Document.tenant_id == tenant_id,
        Document.conversation_id.is_not(None),
        Document.matter_id.is_(None),
        Document.expires_at.is_not(None),
        Document.expires_at <= now,
        or_(
            Document.storage_path.is_(None),
            ~Document.storage_path.ilike("http%"),
        ),
    )
    if not dry_run:
        query = query.with_for_update(skip_locked=True)
    rows = list((await db.scalars(query)).all())
    eligible_bytes = sum(int(row.file_size or 0) for row in rows)
    result: dict[str, Any] = {
        "category": "chat_attachments",
        "eligible_records": len(rows),
        "eligible_bytes": eligible_bytes,
        "deleted_records": 0,
        "deleted_files": 0,
        "already_missing_files": 0,
        "failed_file_deletions": 0,
        "protected": "legal_hold" if legal_hold else None,
        "evaluated_at": now.isoformat(),
    }

    if legal_hold and not dry_run:
        action = RetentionAction(
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            actor_type=actor_type,
            action="cleanup",
            status="blocked",
            dry_run=False,
            legal_hold_at_execution=True,
            policy_version=policy.version if policy else None,
            result_json=result,
        )
        db.add(action)
        await db.commit()
        return result

    if dry_run:
        db.add(
            RetentionAction(
                tenant_id=tenant_id,
                actor_user_id=actor_user_id,
                actor_type=actor_type,
                action="cleanup_preview",
                status="completed",
                dry_run=True,
                legal_hold_at_execution=legal_hold,
                policy_version=policy.version if policy else None,
                result_json=result,
            )
        )
        await db.commit()
        return result

    files = [(row.storage_path, int(row.file_size or 0)) for row in rows]
    action = RetentionAction(
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        actor_type=actor_type,
        action="cleanup",
        status="database_committed",
        dry_run=False,
        legal_hold_at_execution=False,
        policy_version=policy.version if policy else None,
        result_json=result,
    )
    db.add(action)
    for row in rows:
        await db.delete(row)
    result["deleted_records"] = len(rows)
    action.result_json = dict(result)
    await db.flush()
    action_id = action.id
    await db.commit()

    for path, _size in files:
        try:
            outcome = _delete_local_file(tenant_id, path)
            if outcome == "deleted":
                result["deleted_files"] += 1
            elif outcome in {"already_missing", "missing_reference"}:
                result["already_missing_files"] += 1
            else:
                result["failed_file_deletions"] += 1
        except (OSError, ValueError):
            result["failed_file_deletions"] += 1

    await set_tenant_context(db, str(tenant_id))
    persisted = await db.scalar(
        select(RetentionAction).where(RetentionAction.id == action_id)
    )
    if persisted:
        persisted.status = (
            "completed" if result["failed_file_deletions"] == 0 else "partial"
        )
        persisted.result_json = result
    await db.commit()
    return result


def authenticated_request_method(request) -> str:
    if request.headers.get("authorization", "").startswith("Bearer "):
        return "bearer_token"
    if request.cookies.get("access_token"):
        return "session_cookie"
    return "authenticated_session"


def bounded_user_agent(value: str | None) -> str | None:
    normalized = (value or "").strip()
    return normalized[:500] or None


def process_identity() -> str:
    return f"scheduler:{os.getpid()}"
