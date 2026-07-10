from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.operator_audit import OperatorAuditLog
from app.middleware.rate_limit import _client_ip

_SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "encrypted_key",
    "key",
    "password",
    "prompt",
    "query_text",
    "raw_text",
    "request_body",
    "response",
    "response_preview",
    "secret",
    "token",
}


def _safe_scalar(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def sanitize_operator_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text.lower() in _SENSITIVE_KEYS:
                continue
            sanitized[key_text] = sanitize_operator_metadata(item)
        return sanitized
    if isinstance(value, list):
        return [sanitize_operator_metadata(item) for item in value]
    return _safe_scalar(value)


def operator_debug_mode_audit_payload(
    *,
    tenant_id: str,
    conversation_id: str | None = None,
    enabled: bool,
    retention_days: int,
    reason: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "tenant_id": tenant_id,
        "enabled": bool(enabled),
        "retention_days": int(retention_days),
    }
    if conversation_id:
        payload["conversation_id"] = conversation_id
    if reason:
        payload["reason"] = reason[:300]
    return sanitize_operator_metadata(payload)


async def record_operator_audit(
    db: AsyncSession,
    request: Request,
    *,
    action: str,
    resource_type: str | None = None,
    resource_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    actor_type: str = "platform_key",
    actor_id: str | None = None,
) -> OperatorAuditLog:
    ip_address = _client_ip(request)

    entry = OperatorAuditLog(
        action=action,
        actor_type=actor_type,
        actor_id=actor_id,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id is not None else None,
        ip_address=ip_address or None,
        user_agent=(request.headers.get("user-agent") or "")[:500] or None,
        metadata_json=sanitize_operator_metadata(metadata or {}),
    )
    db.add(entry)
    await db.flush()
    return entry
