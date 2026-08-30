"""Pure validation and serialization helpers for operating-trust workflows."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, literal, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

import app.models  # noqa: F401 -- register the complete tenant model inventory
from app.database import Base
from app.models.operating_trust import CustomerLifecycleReceipt

_SENSITIVE_KEYS = {
    "authorization",
    "credential",
    "password",
    "prompt",
    "raw_content",
    "secret",
    "token",
}
_SECURITY_METADATA_ONLY_TABLES = frozenset(
    {
        "api_access_logs",
        "mcp_product_keys",
        "smb_credentials",
        "tenant_credentials",
        "tenant_oauth_apps",
        "tenant_plugin_setups",
        "user_oauth_tokens",
        "users",
        "workspace_mcp_grants",
    }
)
_EVIDENCE_ONLY_TABLES = frozenset(
    {
        "customer_lifecycle_receipts",
        "document_integrity_events",
        "document_storage_operations",
        "offboarding_approvals",
        "retention_actions",
        "tenant_agreement_acceptances",
        "workspace_mcp_audit_events",
    }
)
_UNSAFE_PUBLIC_PATTERNS = (
    re.compile(r"\b(password|secret|token|authorization)\s*[:=]", re.I),
    re.compile(r"\b(?:10\.|127\.|169\.254\.|172\.(?:1[6-9]|2\d|3[01])\.|192\.168\.)"),
    re.compile(r"(?:[A-Za-z]:\\|/etc/|/srv/|/var/lib/)", re.I),
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def assert_safe_evidence(value: Any, *, path: str = "evidence") -> None:
    """Reject common secret/content fields from metadata-only evidence."""

    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in _SENSITIVE_KEYS:
                raise ValueError(f"{path} contains prohibited field: {key}")
            assert_safe_evidence(item, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            assert_safe_evidence(item, path=f"{path}[{index}]")
    elif isinstance(value, str):
        if len(value) > 4000:
            raise ValueError(f"{path} value is too long")
        if any(pattern.search(value) for pattern in _UNSAFE_PUBLIC_PATTERNS):
            raise ValueError(f"{path} contains sensitive or internal detail")


def opaque_evidence_reference(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("evidence reference must not be blank")
    if normalized.startswith(("/", "\\")) or ":\\" in normalized:
        raise ValueError("evidence reference must be an opaque identifier")
    if any(pattern.search(normalized) for pattern in _UNSAFE_PUBLIC_PATTERNS):
        raise ValueError("evidence reference contains sensitive or internal detail")
    return normalized


def assert_public_safe_text(value: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError("public incident text must not be blank")
    if any(pattern.search(normalized) for pattern in _UNSAFE_PUBLIC_PATTERNS):
        raise ValueError("public incident text contains internal or sensitive detail")
    return normalized


def normalized_counts(value: dict[str, int] | None) -> dict[str, int]:
    result: dict[str, int] = {}
    for key, count in sorted((value or {}).items()):
        name = str(key).strip()
        if not name or not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise ValueError("reconciliation counts require named non-negative integers")
        result[name] = count
    return result


def reconcile_counts(
    expected: dict[str, int] | None, actual: dict[str, int] | None
) -> list[dict[str, Any]]:
    left = normalized_counts(expected)
    right = normalized_counts(actual)
    discrepancies: list[dict[str, Any]] = []
    for category in sorted(set(left) | set(right)):
        if category not in right:
            discrepancies.append(
                {
                    "category": category,
                    "expected": left[category],
                    "actual": None,
                    "delta": None,
                    "reason": "missing_category",
                }
            )
            continue
        if category not in left:
            discrepancies.append(
                {
                    "category": category,
                    "expected": None,
                    "actual": right[category],
                    "delta": None,
                    "reason": "unexpected_category",
                }
            )
            continue
        expected_count = left.get(category, 0)
        actual_count = right.get(category, 0)
        if expected_count != actual_count:
            discrepancies.append(
                {
                    "category": category,
                    "expected": expected_count,
                    "actual": actual_count,
                    "delta": actual_count - expected_count,
                }
            )
    return discrepancies


def support_acknowledgement_due(
    started_at: datetime, *, severity: str, objective_minutes: int
) -> datetime:
    """Apply the published continuous S1 / covered-hours S2-S4 clock."""

    if started_at.tzinfo is None:
        raise ValueError("support clock requires a timezone-aware timestamp")
    if severity == "S1":
        return started_at + timedelta(minutes=objective_minutes)
    central = ZoneInfo("America/Chicago")
    cursor = started_at.astimezone(timezone.utc).replace(second=0, microsecond=0)
    remaining = objective_minutes
    while remaining:
        local = cursor.astimezone(central)
        if local.weekday() < 5 and 8 <= local.hour < 17:
            remaining -= 1
        cursor += timedelta(minutes=1)
    return cursor


async def tenant_export_inventory(
    db: AsyncSession, tenant_id, *, retention: dict[str, Any]
) -> dict[str, Any]:
    """Inventory every tenant-id table plus out-of-database file providers.

    The dynamic metadata walk is intentional: a newly introduced tenant table
    becomes a required export category immediately instead of waiting for a
    manually maintained list to catch up.
    """

    counts: dict[str, int] = {}
    tenant_tables: list[str] = []
    categories: list[dict[str, Any]] = []
    tables = [
        table
        for table in sorted(Base.metadata.tables.values(), key=lambda item: item.name)
        if "tenant_id" in table.c
    ]
    count_statements = [
        select(
            literal(table.name).label("table_name"),
            func.count().label("record_count"),
        )
        .select_from(table)
        .where(table.c.tenant_id == tenant_id)
        for table in tables
    ]
    count_rows = (await db.execute(union_all(*count_statements))).all()
    row_counts = {str(name): int(count or 0) for name, count in count_rows}
    for table in tables:
        tenant_tables.append(table.name)
        counts[f"database:{table.name}"] = row_counts[table.name]
        if table.name in _SECURITY_METADATA_ONLY_TABLES:
            export_mode = "security-metadata-only-no-secret-values"
        elif table.name in _EVIDENCE_ONLY_TABLES:
            export_mode = "immutable-evidence-summary"
        else:
            export_mode = "existing-product-export-path"
        categories.append(
            {
                "category": f"database:{table.name}",
                "record_count": counts[f"database:{table.name}"],
                "export_mode": export_mode,
            }
        )

    providers = []
    for provider in retention.get("matter_file_providers", []):
        name = str(provider.get("provider") or "unknown")
        category = f"provider-files:{name}"
        counts[category] = int(provider.get("record_count") or 0)
        providers.append(
            {
                "provider": name,
                "record_count": counts[category],
                "bytes": int(provider.get("bytes") or 0),
                "export_state": "customer-or-provider-path-required",
            }
        )
        categories.append(
            {
                "category": category,
                "record_count": counts[category],
                "export_mode": "customer-or-provider-export-path",
            }
        )

    local = next(
        (
            item
            for item in retention.get("categories", [])
            if item.get("name") == "local_file_references"
        ),
        None,
    )
    counts["file-store:local-references"] = int(
        (local or {}).get("record_count") or 0
    )
    categories.append(
        {
            "category": "file-store:local-references",
            "record_count": counts["file-store:local-references"],
            "export_mode": "existing-file-export-path",
        }
    )
    return {
        "schema": "lawhand.tenant-export-inventory",
        "contract_version": None,
        "counts": dict(sorted(counts.items())),
        "categories": sorted(categories, key=lambda item: item["category"]),
        "tenant_table_count": len(tenant_tables),
        "providers": providers,
        "retention_policy_version": int(retention.get("policy_version") or 0),
        "legal_hold": bool(retention.get("legal_hold")),
        "boundary": "This inventory accounts for current tenant-id database tables and recorded file providers. Secret values are never exported; security and immutable evidence categories use bounded summaries. Provider availability and artifact format remain explicit scope.",
    }


def evidence_hash(payload: Any) -> str:
    assert_safe_evidence(payload)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def receipt_hash(payload: dict[str, Any]) -> str:
    return evidence_hash(payload)


def receipt_payload(row: CustomerLifecycleReceipt) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "tenant_id": str(row.tenant_id),
        "receipt_type": row.receipt_type,
        "contract_version": row.contract_version,
        "status": row.status,
        "scope": row.scope_json,
        "expected_counts": row.expected_counts,
        "actual_counts": row.actual_counts,
        "discrepancies": row.discrepancies,
        "source_import_run_id": (
            str(row.source_import_run_id) if row.source_import_run_id else None
        ),
        "artifact_reference": row.artifact_reference,
        "artifact_sha256": row.artifact_sha256,
        "signer": {
            "name": row.signer_name,
            "email": row.signer_email,
            "title": row.signer_title,
            "actor_type": row.signer_actor_type,
            "authority_attested": row.authority_attested,
        },
        "approvals": row.approvals_json,
        "legal_hold_snapshot": row.legal_hold_snapshot,
        "provider_data": row.provider_data_json,
        "backup_expiry": row.backup_expiry_json,
        "outcome": row.outcome,
        "receipt_hash": row.receipt_hash,
        "created_at": row.created_at,
    }


_SUPPORT_TRANSITIONS = {
    "open": {"acknowledged"},
    "acknowledged": {"mitigated", "resolved"},
    "mitigated": {"resolved"},
    "resolved": set(),
}


def assert_support_transition(current: str, target: str) -> None:
    if target not in _SUPPORT_TRANSITIONS.get(current, set()):
        raise ValueError(f"invalid support transition: {current} -> {target}")


_INCIDENT_TRANSITIONS = {
    "investigating": {"identified", "monitoring", "resolved"},
    "identified": {"monitoring", "resolved"},
    "monitoring": {"identified", "resolved"},
    "resolved": set(),
}


def assert_incident_transition(current: str, target: str) -> None:
    if target not in _INCIDENT_TRANSITIONS.get(current, set()):
        raise ValueError(f"invalid incident transition: {current} -> {target}")
