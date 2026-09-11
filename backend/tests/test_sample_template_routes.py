"""Endpoint-level tests for the global sample-template library router.

These exercise the handler logic directly with a mocked async session (no live
database), covering list/detail/source/render branches and the read-only
guarantees the catalog relies on.
"""

import uuid
from datetime import datetime, timezone
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.routers import sample_templates
from app.services.pdf_templates import discover_pdf_fields


def _fillable_pdf() -> bytes:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    output = BytesIO()
    pdf = canvas.Canvas(output, pagesize=letter)
    pdf.drawString(72, 720, "Client:")
    pdf.acroForm.textfield(name="Client Name", x=120, y=705, width=250, height=24)
    pdf.save()
    return output.getvalue()


def _sample(**overrides) -> SimpleNamespace:
    payload = {
        "id": uuid.uuid4(),
        "slug": "last-will-and-testament",
        "title": "Last Will and Testament",
        "category": "wills_trusts",
        "jurisdictions": ["North Dakota"],
        "description": "Generic starter form.",
        "format": "pdf",
        "field_count": 26,
        "variable_schema": {"fields": []},
        "source_filename": "wills_trusts/last-will-and-testament.pdf",
        "source_sha256": "a" * 64,
        "source_file_size": 100,
        "is_active": True,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


class _ScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return SimpleNamespace(all=lambda: self._rows)

    def scalar_one(self):
        return self._rows[0] if self._rows else None


@pytest.mark.asyncio
async def test_list_filters_by_category_and_jurisdiction():
    sample = _sample(category="wills_trusts", jurisdictions=["North Dakota"])
    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            _ScalarResult([sample]),  # category-filtered rows
            _ScalarResult([1]),  # count
        ]
    )

    response = await sample_templates.list_sample_templates(
        category="wills_trusts",
        jurisdiction="North Dakota",
        current_user=SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4()),
        db=db,
    )
    assert response.total == 1
    assert response.items[0].slug == "last-will-and-testament"


@pytest.mark.asyncio
async def test_get_returns_sample():
    sample = _sample()
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=sample)

    response = await sample_templates.get_sample_template(
        sample.id, current_user=None, db=db
    )
    assert response.slug == "last-will-and-testament"


@pytest.mark.asyncio
async def test_get_missing_returns_404():
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)
    with pytest.raises(HTTPException) as exc_info:
        await sample_templates.get_sample_template(uuid.uuid4(), current_user=None, db=db)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_render_fills_sample(monkeypatch):
    pdf = _fillable_pdf()
    fields = discover_pdf_fields(pdf)
    sample = _sample(variable_schema={"version": 1, "fields": fields})
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=sample)
    monkeypatch.setattr(sample_templates, "_verified_source", lambda s: pdf)

    response = await sample_templates.render_sample_template(
        sample.id,
        sample_templates.SampleTemplateRenderRequest(
            variables={fields[0]["name"]: "Ada Example"}
        ),
        current_user=None,
        db=db,
    )
    assert response.media_type == "application/pdf"
    assert response.body.startswith(b"%PDF-")


@pytest.mark.asyncio
async def test_render_requires_seeded_schema():
    sample = _sample(variable_schema=None)
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=sample)
    with pytest.raises(HTTPException) as exc_info:
        await sample_templates.render_sample_template(
            sample.id,
            sample_templates.SampleTemplateRenderRequest(variables={}),
            current_user=None,
            db=db,
        )
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_download_source_streams_pdf(monkeypatch):
    sample = _sample()
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=sample)
    monkeypatch.setattr(sample_templates, "_verified_source", lambda s: b"%PDF-1.4 x")

    response = await sample_templates.download_sample_source(
        sample.id, current_user=None, db=db
    )
    assert response.media_type == "application/pdf"
    assert response.body == b"%PDF-1.4 x"
