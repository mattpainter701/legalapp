import io
import zipfile
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.chat_action import (
    GetMatterContextArgs,
    GetMatterDocumentTextArgs,
    ListMatterDocumentsArgs,
)
from app.services.automation_capabilities import (
    CapabilityError,
    resolve_capability_spec,
)
from app.services.matter_workspace_capabilities import (
    _document_summary,
    _document_text_format,
    _matter_summary,
    _template_automation_ready,
    _template_rank,
    _validate_docx_archive,
)


def _matter(**overrides):
    values = {
        "id": uuid4(),
        "slug": "smith-v-jones",
        "matter_name": "Smith v. Jones",
        "description": "d" * 3_000,
        "matter_type": "litigation",
        "practice_area": "civil",
        "role": "plaintiff",
        "counterparty": "Jones",
        "jurisdiction": "North Dakota",
        "court": "District Court",
        "judge": "Hon. Example",
        "case_number": "CV-2026-100",
        "status": "open",
        "stage": "discovery",
        "risk_level": "medium",
        "materiality": "high",
        "exposure_range": None,
        "legal_hold_issued": False,
        "key_dates": {
            f"date-{index:02}": f"2026-09-{index + 1:02}" for index in range(20)
        },
        "initial_posture": "p" * 3_000,
        "decision": None,
        "is_closed": False,
        "outcome": None,
        "primary_plugin": None,
        "attorney_of_record_id": uuid4(),
        "memory_content": "m" * 5_000,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_matter_context_arguments_are_bounded_to_safe_sections():
    args = GetMatterContextArgs.model_validate({"matter_id": str(uuid4())})

    assert args.sections == ["team", "tasks", "events", "notes"]
    assert args.max_items_per_section == 10
    expanded = GetMatterContextArgs.model_validate(
        {
            "matter_id": str(uuid4()),
            "sections": ["client", "parties", "documents", "communications"],
        }
    )
    assert expanded.sections == [
        "client",
        "parties",
        "documents",
        "communications",
    ]
    with pytest.raises(ValidationError):
        GetMatterContextArgs.model_validate(
            {"matter_id": str(uuid4()), "sections": ["billing"]}
        )
    with pytest.raises(ValidationError):
        GetMatterContextArgs.model_validate(
            {"matter_id": str(uuid4()), "max_items_per_section": 26}
        )


def test_matter_summary_caps_text_and_key_dates():
    summary = _matter_summary(_matter())

    assert len(summary["description"]) <= 2_000
    assert len(summary["initial_posture"]) <= 2_000
    assert len(summary["memory"]) <= 4_000
    assert len(summary["key_dates"]) == 12
    assert "budget_amount" not in summary
    assert "cloud_folder" not in summary


def test_document_metadata_never_exposes_provider_storage_fields():
    document_id = uuid4()
    matter_id = uuid4()
    document = SimpleNamespace(
        id=document_id,
        matter_id=matter_id,
        filename="motion.docx",
        description="Draft motion",
        document_category="pleading",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        file_size=1_024,
        task_id=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        storage_path="C:/secret/provider/path",
        provider_object_id="provider-secret",
    )

    summary = _document_summary(document)

    assert summary["document_id"] == str(document_id)
    assert summary["download_url"].endswith(f"/{document_id}/download")
    assert "storage_path" not in summary
    assert "provider_object_id" not in summary
    assert "storage_backend" not in summary


def test_document_list_contract_has_a_hard_limit():
    with pytest.raises(ValidationError):
        ListMatterDocumentsArgs.model_validate({"matter_id": str(uuid4()), "limit": 51})


def test_document_text_contract_has_character_and_page_limits():
    valid = GetMatterDocumentTextArgs.model_validate(
        {"matter_id": str(uuid4()), "document_id": str(uuid4())}
    )

    assert valid.max_characters == 20_000
    assert valid.max_pdf_pages == 20
    with pytest.raises(ValidationError):
        GetMatterDocumentTextArgs.model_validate(
            {
                "matter_id": str(uuid4()),
                "document_id": str(uuid4()),
                "max_characters": 50_001,
            }
        )
    with pytest.raises(ValidationError):
        GetMatterDocumentTextArgs.model_validate(
            {
                "matter_id": str(uuid4()),
                "document_id": str(uuid4()),
                "max_pdf_pages": 51,
            }
        )


def test_document_text_rejects_unsupported_binary_formats():
    document = SimpleNamespace(
        filename="evidence.exe", content_type="application/octet-stream"
    )

    with pytest.raises(CapabilityError) as caught:
        _document_text_format(document)

    assert caught.value.code == "unsupported_document_format"


def test_docx_validation_rejects_malformed_archives():
    with pytest.raises(CapabilityError) as caught:
        _validate_docx_archive(b"not a zip archive")

    assert caught.value.code == "invalid_document"


def test_docx_validation_rejects_encrypted_entries(monkeypatch):
    class FakeArchive:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def infolist(self):
            return [
                SimpleNamespace(flag_bits=1, file_size=1, filename="word/document.xml")
            ]

    monkeypatch.setattr(zipfile, "ZipFile", lambda *_args, **_kwargs: FakeArchive())

    with pytest.raises(CapabilityError) as caught:
        _validate_docx_archive(b"ignored")

    assert caught.value.code == "unsafe_document_archive"


def test_docx_validation_rejects_excessive_uncompressed_size(monkeypatch):
    from app.services import matter_workspace_capabilities as capabilities

    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, "w") as archive:
        archive.writestr("[Content_Types].xml", "types")
        archive.writestr("word/document.xml", "x" * 64)
    monkeypatch.setattr(capabilities, "_MAX_DOCX_UNCOMPRESSED_BYTES", 32)

    with pytest.raises(CapabilityError) as caught:
        _validate_docx_archive(archive_bytes.getvalue())

    assert caught.value.code == "unsafe_document_archive"


@pytest.mark.parametrize(
    ("status", "format", "approved", "source", "sha256", "body", "ready"),
    [
        ("draft", "markdown", False, False, False, True, False),
        ("approved", "markdown", False, False, False, True, True),
        ("approved", "pdf", False, True, True, True, False),
        ("approved", "pdf", True, True, True, True, True),
        ("approved", "docx", False, True, False, True, False),
    ],
)
def test_template_recommendations_only_use_automation_ready_templates(
    status, format, approved, source, sha256, body, ready
):
    template = SimpleNamespace(
        status=status,
        format=format,
        approved_at=datetime.now(timezone.utc) if approved else None,
        source_storage_path="source.docx" if source else None,
        source_sha256="a" * 64 if sha256 else None,
        body="Body" if body else "",
    )

    assert _template_automation_ready(template) is ready


def test_template_rank_is_deterministic_and_explainable():
    matter = _matter(primary_plugin="domestic")
    template = SimpleNamespace(
        jurisdiction="North Dakota",
        stage="discovery",
        module="domestic",
        source_storage_path="source.docx",
    )

    score, reasons = _template_rank(template, matter)

    assert score == 15
    assert reasons == ["jurisdiction", "stage", "workflow", "source-backed"]


def test_new_reads_have_separate_document_and_template_scopes():
    context_spec = resolve_capability_spec("get_matter_context")
    document_spec = resolve_capability_spec("list_matter_documents")
    content_spec = resolve_capability_spec("get_matter_document_text")
    template_spec = resolve_capability_spec("list_document_templates")

    assert context_spec.required_scopes == ("matters:read", "tasks:read")
    assert document_spec.required_scopes == ("matters:read", "documents:read")
    assert content_spec.required_scopes == ("matters:read", "documents:read")
    assert template_spec.required_scopes == ("matters:read", "templates:read")
