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
