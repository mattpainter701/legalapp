"""Native e-signature service package.

Exposes a small, swappable provider interface plus the ``internal`` provider
(portal typed-signature capture). Orchestration helpers (recording a signature
and finalizing a completed request into an executed-copy document) live in
``service.py``.
"""

from app.services.esign.base import ESignProvider, get_provider
from app.services.esign.service import (
    complete_request_if_done,
    record_portal_signature,
)

__all__ = [
    "ESignProvider",
    "get_provider",
    "complete_request_if_done",
    "record_portal_signature",
]
