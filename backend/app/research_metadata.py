"""Bounded public research metadata shared by prompts and source ledgers."""

import re
from datetime import datetime


def research_source_metadata(source: dict) -> dict:
    """Bound untrusted metadata; source dates describe capture, not legal validity."""
    result = {}
    for key in ("source_jurisdiction", "document_status"):
        value = source.get(key)
        result[key] = (
            value[:40]
            if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_ -]{1,40}", value)
            else None
        )
    for key in ("retrieved_at", "last_successful_sync_at", "termination_date"):
        value = source.get(key)
        try:
            if value and isinstance(value, str) and len(value) <= 40:
                datetime.fromisoformat(value)
                result[key] = value
            else:
                result[key] = None
        except (TypeError, ValueError):
            result[key] = None
    return result
