"""Bind encrypted runtime payloads to their tenant, run and step."""

import json
import re
import uuid

from app.services.configurable_workflows import digest_payload
from app.services.token_vault import decrypt_token, encrypt_token

MAX_RESULT_BYTES = 1024 * 1024
IDENTITY_FIELDS = frozenset(
    {
        "task_id",
        "artifact_id",
        "artifact_revision_id",
        "artifact_revision_no",
        "artifact_sha256",
        "document_id",
        "document_sha256",
        "matter_id",
        "template_id",
        "client_request_id",
    }
)
STATE_FIELDS = frozenset({"status", "action_type", "review_policy", "review_stage"})


def canonical_payload(value):
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )
    if len(encoded.encode()) > MAX_RESULT_BYTES:
        raise ValueError("Capability result exceeds the durable-run payload budget")
    return json.loads(encoded)


def seal_payload(payload, *, tenant_id, run_id, step_id, kind):
    value = canonical_payload(payload)
    envelope = dict(
        tenant_id=str(tenant_id),
        run_id=str(run_id),
        step_id=str(step_id),
        kind=kind,
        payload=value,
    )
    return encrypt_token(
        json.dumps(envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    ), digest_payload(value)


def open_payload(ciphertext, *, tenant_id, run_id, step_id, kind, expected_sha256):
    envelope = json.loads(decrypt_token(ciphertext))
    expected = dict(
        tenant_id=str(tenant_id), run_id=str(run_id), step_id=str(step_id), kind=kind
    )
    if any(envelope.get(key) != value for key, value in expected.items()):
        raise ValueError("Runtime payload identity does not match its ledger")
    payload = canonical_payload(envelope["payload"])
    if digest_payload(payload) != expected_sha256:
        raise ValueError("Runtime payload fingerprint does not match its ledger")
    return payload


def result_summary(result):
    """Expose identities, states and shape, never work-product text or URLs."""
    summary = {}
    for key in IDENTITY_FIELDS:
        value = result.get(key)
        if key.endswith("_no"):
            if type(value) is int and 0 < value < 1000000:
                summary[key] = value
        elif key.endswith("sha256"):
            if isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value):
                summary[key] = value
        elif isinstance(value, str):
            try:
                summary[key] = str(uuid.UUID(value))
            except ValueError:
                pass
    for key in STATE_FIELDS:
        value = result.get(key)
        if (
            isinstance(value, str)
            and len(value) <= 50
            and value.replace("_", "").isalnum()
        ):
            summary[key] = value
    summary["result_fields"] = sorted(str(key)[:100] for key in result)[:50]
    summary["result_counts"] = {
        str(key)[:100]: len(value)
        for key, value in result.items()
        if isinstance(value, list)
    }
    return summary
