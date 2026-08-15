"""Verified, global user context for model prompts.

This deliberately excludes ``memory_summary`` and ``UserMemory``.  Learned
memory has a separate lifecycle and prompt section, while these fields are
explicitly supplied and managed by the user or an authorized administrator.
"""

from __future__ import annotations

from typing import Any

from app.utils.guardrails import prepare_provider_text


def build_global_user_context(user: Any, *, privacy_mode: bool | None = None) -> str:
    """Return a compact, structured verified profile safe for provider prompts.

    Authorization ``user.role`` is intentionally never used as a professional
    role: it controls application access and is not a statement of profession.
    """
    privacy_mode = bool(
        getattr(user, "privacy_mode", False)
        if privacy_mode is None
        else privacy_mode
    )
    values = (
        ("Name", getattr(user, "full_name", None)),
        ("Email", getattr(user, "email", None)),
        ("Professional role", getattr(user, "professional_role", None)),
        ("Job title", getattr(user, "job_title", None)),
        ("Office location", getattr(user, "office_location", None)),
        (
            "Primary jurisdictions",
            getattr(user, "primary_jurisdictions", None) or [],
        ),
        ("Practice areas", getattr(user, "practice_areas", None) or []),
        ("Experience level", getattr(user, "expertise_level", None)),
    )
    lines = ["Verified user profile (not learned memory):"]
    for label, value in values:
        if isinstance(value, list):
            rendered = ", ".join(
                str(item).strip() for item in value if str(item).strip()
            )
        else:
            rendered = str(value).strip() if value else ""
        if rendered:
            safe_value = prepare_provider_text(rendered, privacy_mode)
            lines.append(f"- {label}: {safe_value}")
    return "\n".join(lines) if len(lines) > 1 else "No verified user profile available."
