"""Native e-signature service package.

Exposes a small, swappable provider interface plus the ``internal`` provider
(portal typed-signature capture). Orchestration helpers (recording a signature
and finalizing a completed request into an executed-copy document) live in
``service.py``.
"""

from app.services.esign.base import ESignProvider, get_provider
from app.services.esign.service import (
    complete_request_if_done,
    mark_request_expired_if_needed,
    next_pending_signers,
    record_portal_decline,
    record_portal_signature,
    signer_can_act_now,
)

__all__ = [
    "ESignProvider",
    "get_provider",
    "complete_request_if_done",
    "mark_request_expired_if_needed",
    "next_pending_signers",
    "record_portal_decline",
    "record_portal_signature",
    "signer_can_act_now",
]
