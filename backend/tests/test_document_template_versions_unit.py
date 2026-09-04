"""Recording immutable template versions."""

import uuid

import pytest

from app.services.document_template_versions import (
    VERSIONED_FIELDS,
    body_sha256,
    snapshot_differs,
)


class _Template:
    def __init__(self, **overrides):
        self.id = uuid.uuid4()
        self.title = "Engagement letter"
        self.body = "Dear {{client}},"
        self.variable_schema = {"fields": [{"name": "client"}]}
        self.format = "markdown"
        self.category = "other"
        self.source_sha256 = None
        self.source_filename = None
        self.is_active = True
        self.current_version_no = 0
        for key, value in overrides.items():
            setattr(self, key, value)


class TestBodyDigest:
    def test_digest_is_stable_and_handles_an_absent_body(self):
        assert body_sha256("abc") == body_sha256("abc")
        assert body_sha256(None) == body_sha256("")
        assert len(body_sha256("abc")) == 64

    def test_different_bodies_differ(self):
        assert body_sha256("a") != body_sha256("b")


class TestSnapshotDiffers:
    @pytest.mark.parametrize(
        "updates",
        [
            {"title": "Renamed"},
            {"body": "New wording"},
            {"variable_schema": {"fields": []}},
            {"format": "docx"},
            {"category": "litigation"},
            {"is_active": False},
            {"source_sha256": "a" * 64},
        ],
    )
    def test_a_change_to_any_versioned_field_records_history(self, updates):
        assert snapshot_differs(_Template(), updates) is True

    @pytest.mark.parametrize(
        "updates",
        [
            {},
            {"description": "A note"},
            {"jurisdiction": "CA"},
            {"module": "probate"},
            # Same value submitted again is not a change.
            {"title": "Engagement letter"},
            {"is_active": True},
        ],
    )
    def test_cosmetic_or_no_op_edits_do_not_manufacture_history(self, updates):
        assert snapshot_differs(_Template(), updates) is False

    def test_versioned_fields_cover_everything_a_version_row_stores(self):
        # A field recorded on the version row but absent here would let an
        # edit change what a version claims without recording that it did.
        assert set(VERSIONED_FIELDS) == {
            "title",
            "body",
            "variable_schema",
            "format",
            "category",
            "source_sha256",
            "is_active",
        }
