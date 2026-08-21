from __future__ import annotations

import pytest

from app.models.document_integrity_event import DocumentIntegrityEvent
from app.services.document_accountability import (
    DocumentAccountabilityError,
    bounded_integrity_metadata,
    integrity_event_sha256,
)


def test_integrity_metadata_is_bounded_and_rejects_secret_or_link_fields():
    assert bounded_integrity_metadata({"revision": 2, "provider": "microsoft"}) == {
        "revision": 2,
        "provider": "microsoft",
    }
    with pytest.raises(DocumentAccountabilityError, match="not permitted"):
        bounded_integrity_metadata({"access_token": "secret"})
    with pytest.raises(DocumentAccountabilityError, match="not permitted"):
        bounded_integrity_metadata({"document_url": "https://example.invalid"})


def test_integrity_hash_is_canonical_and_content_sensitive():
    first = integrity_event_sha256({"b": 2, "a": 1})
    reordered = integrity_event_sha256({"a": 1, "b": 2})
    changed = integrity_event_sha256({"a": 1, "b": 3})

    assert first == reordered
    assert first != changed
    assert len(first) == 64


def test_integrity_events_have_a_unique_positive_tenant_chain_position():
    table = DocumentIntegrityEvent.__table__
    constraints = {constraint.name for constraint in table.constraints}

    assert "uq_document_integrity_events_tenant_chain_position" in constraints
    assert "ck_doc_integrity_events_chain_position" in constraints
    assert table.c.chain_position.nullable is False
