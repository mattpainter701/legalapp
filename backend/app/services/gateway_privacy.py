from __future__ import annotations

from typing import Any

from app.config import get_settings

METADATA_FIELDS = (
    "tenant_id",
    "user_id",
    "conversation_id",
    "operation_type",
    "matter_id",
    "plugin",
    "skill",
    "premium",
    "route_tier",
    "actor_type",
    # Opaque broker-generated correlation id. It contains no customer content
    # and lets spend-log reconciliation survive a missing provider response.
    "request_id",
)


def raw_text_retention_enabled() -> bool:
    return bool(get_settings().GATEWAY_RAW_TEXT_RETENTION_ENABLED)


def retained_gateway_query_text(
    text: str | None, *, max_chars: int = 2000
) -> str | None:
    if not raw_text_retention_enabled() or not text:
        return None
    return text[:max_chars]


def retained_debug_text(text: str | None, *, max_chars: int = 2000) -> str | None:
    if not raw_text_retention_enabled() or not text:
        return None
    return text[:max_chars]


def gateway_metadata(**values: Any) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for field in METADATA_FIELDS:
        value = values.get(field)
        if value is None:
            continue
        metadata[field] = str(value) if field != "premium" else bool(value)
    return metadata


def litellm_metadata(**values: Any) -> dict[str, Any]:
    """Sanitize metadata and retain the opaque id in LiteLLM spend logs.

    LiteLLM intentionally filters arbitrary request metadata before persisting
    spend rows. ``spend_logs_metadata`` is its documented, typed escape hatch
    for metadata-only cost correlation. Callers cannot inject that container:
    it is created only after the allowlist above has been applied.
    """

    metadata = gateway_metadata(**values)
    if "request_id" not in metadata:
        return metadata
    return {**metadata, "spend_logs_metadata": dict(metadata)}
