"""Database-free checks for bounded Studio contracts and redaction."""

import uuid

import pytest
from pydantic import ValidationError

from app.schemas.studio_draft import StudioDraftCreate
from app.services.studio_drafts import (
    StudioError,
    _bounded_redacted,
    _field_definition,
    _sha256,
)


def _base_create():
    field_id = uuid.uuid4()
    return {
        "title": "Template",
        "format": "markdown",
        "source_sha256": "a" * 64,
        "source_media_type": "text/markdown",
        "fields": [
            {
                "id": field_id,
                "automation_key": "client_name",
                "label": "Client",
                "field_type": "text",
            }
        ],
        "placements": [
            {
                "id": uuid.uuid4(),
                "field_id": field_id,
                "format": "markdown",
                "anchor_kind": "template_token",
                "anchor": {"token": "client_name"},
            }
        ],
    }


def test_create_contract_requires_unique_keys_and_local_placement_fields():
    payload = _base_create()
    payload["fields"].append({**payload["fields"][0], "id": uuid.uuid4()})
    with pytest.raises(ValidationError, match="automation keys must be unique"):
        StudioDraftCreate.model_validate(payload)

    payload = _base_create()
    payload["placements"][0]["field_id"] = uuid.uuid4()
    with pytest.raises(ValidationError, match="same request"):
        StudioDraftCreate.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"value": "raw client value"},
        {"source_text": "document excerpt"},
        {"provider_id": "external-item"},
        {"signed_url": "https://storage.invalid/token"},
    ],
)
def test_durable_payload_rejects_raw_values_text_and_provider_references(payload):
    with pytest.raises(StudioError) as caught:
        _bounded_redacted(payload)
    assert caught.value.detail["code"] == "unsafe_durable_payload"


def test_field_definition_is_a_bounded_vocabulary():
    assert _field_definition({"max_length": 200, "multiline": False}) == {
        "max_length": 200,
        "multiline": False,
    }
    with pytest.raises(StudioError) as unknown:
        _field_definition({"document_excerpt": "sensitive"})
    assert unknown.value.detail["code"] == "unsupported_field_definition_key"
    with pytest.raises(StudioError) as reserved:
        _field_definition({"name": "override"})
    assert reserved.value.detail["code"] == "reserved_field_definition_key"


def test_canonical_hash_is_order_independent_and_content_sensitive():
    assert _sha256({"b": 2, "a": 1}) == _sha256({"a": 1, "b": 2})
    assert _sha256({"a": 1}) != _sha256({"a": 2})
