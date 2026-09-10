"""Global sample-template library: catalog integrity and read-only isolation.

These tests run without a live database. They prove the committed catalog is
coherent (every manifest entry maps to a real, renderable PDF whose SHA-256 and
field count match) and that the catalog is platform-owned read-only content —
no ``tenant_id`` column and no tenant mutation endpoints.
"""

import hashlib
import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.models.sample_template import SampleTemplate
from app.routers import sample_templates
from app.services.pdf_templates import TemplatePdfError, discover_pdf_fields

SEED_DIR = Path(__file__).resolve().parents[1] / "seed" / "sample_templates"


def _manifest() -> dict:
    return json.loads((SEED_DIR / "manifest.json").read_text(encoding="utf-8"))


def test_manifest_is_populated_with_unique_slugs():
    forms = _manifest()["forms"]
    assert forms, "the sample-template manifest must not be empty"
    slugs = [form["slug"] for form in forms]
    assert len(slugs) == len(set(slugs)), "sample slugs must be unique"


def test_every_sample_source_matches_manifest_and_is_renderable():
    forms = _manifest()["forms"]
    for form in forms:
        source = SEED_DIR / form["filename"]
        assert source.is_file(), f"missing source for {form['slug']}"
        content = source.read_bytes()
        assert (
            hashlib.sha256(content).hexdigest() == form["sha256"]
        ), f"sha256 mismatch for {form['slug']}"
        try:
            fields = discover_pdf_fields(content)
        except TemplatePdfError as exc:
            raise AssertionError(
                f"{form['slug']} is not renderable by the studio: {exc}"
            ) from exc
        assert fields, f"{form['slug']} has no discovered fields"
        assert len(fields) == form["field_count"], (
            f"{form['slug']} field count drifted: "
            f"manifest={form['field_count']} actual={len(fields)}"
        )


def test_catalog_is_platform_owned_and_not_tenant_scoped():
    # Shared content must not carry a tenant_id; tenants read the same rows.
    column_names = [column.name for column in SampleTemplate.__table__.columns]
    assert "tenant_id" not in column_names


def test_catalog_router_exposes_no_tenant_mutation_endpoints():
    mutating = {"PUT", "PATCH", "DELETE"}
    for route in sample_templates.router.routes:
        methods = {method for method in (getattr(route, "methods", None) or set())}
        assert not (methods & mutating), (
            f"sample catalog must be read-only, got {methods} on {route.path}"
        )


def test_safe_generated_filename_sanitizes():
    assert sample_templates._safe_generated_filename("A/B:C", "pdf") == "A_B_C.pdf"
    assert sample_templates._safe_generated_filename("..", "pdf") == "sample.pdf"


def test_verified_source_rejects_sha256_mismatch(tmp_path, monkeypatch):
    payload = b"%PDF-1.4 fake"
    (tmp_path / "sample.pdf").write_bytes(payload)
    monkeypatch.setattr(sample_templates.settings, "SAMPLE_TEMPLATE_DIR", str(tmp_path))

    class _Sample:
        source_filename = "sample.pdf"
        source_sha256 = hashlib.sha256(b"other").hexdigest()

    with pytest.raises(HTTPException) as exc_info:
        sample_templates._verified_source(_Sample())
    assert exc_info.value.status_code == 409
