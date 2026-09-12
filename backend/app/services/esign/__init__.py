"""Native e-signature service package.

Exposes the ``internal`` provider (in-document signing in the client portal)
behind a small provider interface. Orchestration helpers (recording a
signature or an uploaded signed copy, finalizing a completed request into an
executed copy and evidence certificate, retrying failed filing) live in
``service.py``; where a signer signs is decided in ``plan.py`` and the executed
PDF is drawn in ``render.py``.
"""

from app.services.esign.base import ESignProvider, get_provider
from app.services.esign.service import (
    accept_submission,
    after_completion,
    awaiting_review,
    complete_request_if_done,
    completion_pending,
    decline_event,
    mark_request_expired_if_needed,
    next_pending_signers,
    record_portal_decline,
    record_portal_signature,
    record_uploaded_copy,
    reject_submission,
    retry_pending_completions,
    signer_can_act_now,
)

__all__ = [
    "ESignProvider",
    "get_provider",
    "accept_submission",
    "after_completion",
    "awaiting_review",
    "complete_request_if_done",
    "completion_pending",
    "decline_event",
    "mark_request_expired_if_needed",
    "next_pending_signers",
    "record_portal_decline",
    "record_portal_signature",
    "record_uploaded_copy",
    "reject_submission",
    "retry_pending_completions",
    "signer_can_act_now",
]
