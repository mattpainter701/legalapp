"""Helpers for building safe SQL filter fragments from user-supplied text."""

from __future__ import annotations

# Backslash first: escaping it after the wildcards would double-escape the
# backslashes this function itself introduces.
_LIKE_SPECIALS = ("\\", "%", "_")


def escape_like(value: str) -> str:
    """Escape LIKE/ILIKE wildcards so search text matches literally.

    Search terms are parameterized, so an unescaped ``%`` is not an injection
    risk -- but it is still a wildcard, which makes a search for a literal
    ``%`` or ``_`` silently match far more than the user asked for. Callers must
    pair this with ``escape="\\\\"`` on the ``ilike``/``like`` call.
    """
    result = value
    for character in _LIKE_SPECIALS:
        result = result.replace(character, f"\\{character}")
    return result
