from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

import app.services.matter_workspace_capabilities as workspace
from app.schemas.chat_action import (
    GetMatterContextArgs,
    GetMatterDocumentTextArgs,
    ListDocumentTemplatesArgs,
    ListMatterDocumentsArgs,
)
from app.services.automation_capabilities import CapabilityContext, CapabilityError
from app.services.matter_file_store import MatterFileReadError


class _Result:
    def __init__(self, values):
        self.values = values

    def all(self):
        return self.values

    def scalars(self):
        return self


class _DB:
    def __init__(self, *, scalar_values=(), result_values=()):
        self.scalar_values = list(scalar_values)
        self.result_values = list(result_values)
        self.statements = []

    async def scalar(self, statement):
        self.statements.append(statement)
        return self.scalar_values.pop(0)

    async def execute(self, statement):
        self.statements.append(statement)
        return _Result(self.result_values.pop(0))


def _matter(*, tenant_id, matter_id=None, **overrides):
    values = {
        "id": matter_id or uuid4(),
        "tenant_id": tenant_id,
        "slug": "smith-v-jones",
        "matter_name": "Smith v. Jones",
        "description": "A bounded litigation summary",
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
        "key_dates": {"hearing": date(2026, 9, 3)},
        "initial_posture": "Complaint filed",
        "decision": None,
        "is_closed": False,
        "outcome": None,
        "primary_plugin": "domestic",
        "attorney_of_record_id": uuid4(),
        "memory_content": "Firm-curated matter memory",
        "client_contact_id": None,
        "created_at": datetime(2026, 8, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 8, 2, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _context(db, tenant_id):
    return CapabilityContext(
        db=db,
        user=SimpleNamespace(id=uuid4(), tenant_id=tenant_id),
        granted_scopes=frozenset({"matters:read", "documents:read", "templates:read"}),
    )


def _document(*, matter_id, tenant_id, **overrides):
    values = {
        "id": uuid4(),
        "tenant_id": tenant_id,
        "matter_id": matter_id,
        "filename": "motion.txt",
        "description": "Draft motion",
        "document_category": "pleading",
        "content_type": "text/plain",
        "file_size": 250,
        "task_id": uuid4(),
        "created_at": datetime(2026, 8, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 8, 2, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_get_matter_context_executes_every_consentable_section():
    tenant_id = uuid4()
    matter = _matter(tenant_id=tenant_id)
    assignment = SimpleNamespace(
        role="paralegal", is_primary=True, is_active_working=True
    )
    team_user = SimpleNamespace(id=uuid4(), full_name="Pat Paralegal")
    task = SimpleNamespace(
        id=uuid4(),
        title="Review discovery",
        description="Check answers",
        status="in_progress",
        priority="high",
        due_date=date(2026, 9, 1),
        assigned_to_user_id=team_user.id,
        reviewer_user_id=uuid4(),
        source="assistant",
    )
    event = SimpleNamespace(
        id=uuid4(),
        event_type="filing",
        title="Complaint filed",
        content="Court event content",
        note_type=None,
        created_at=datetime(2026, 8, 3, tzinfo=timezone.utc),
    )
    note = SimpleNamespace(
        id=uuid4(),
        note_type="strategy",
        title="Next steps",
        content="Prepare written discovery",
        author_id=team_user.id,
        created_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
    )
    db = _DB(
        scalar_values=[matter],
        result_values=[[(assignment, team_user)], [task], [event], [note]],
    )
    args = GetMatterContextArgs(
        matter_id=matter.id,
        sections=["team", "tasks", "events", "notes"],
        max_items_per_section=2,
    )

    payload = await workspace.get_matter_context(_context(db, tenant_id), args)

    assert payload["matter"]["matter_id"] == str(matter.id)
    assert payload["team"][0]["name"] == "Pat Paralegal"
    assert payload["open_tasks"][0]["assigned_to_user_id"] == str(team_user.id)
    assert payload["events"][0]["event_type"] == "filing"
    assert payload["notes"][0]["author_user_id"] == str(team_user.id)
    assert payload["limits"]["max_items_per_section"] == 2
    assert "untrusted source material" in payload["content_warning"]


@pytest.mark.asyncio
async def test_get_matter_context_hides_missing_or_foreign_matter_ids():
    tenant_id = uuid4()
    args = GetMatterContextArgs(matter_id=uuid4())

    with pytest.raises(CapabilityError) as exc_info:
        await workspace.get_matter_context(
            _context(_DB(scalar_values=[None]), tenant_id), args
        )

    assert exc_info.value.code == "matter_not_found"
    assert exc_info.value.message == "Matter not found"


@pytest.mark.asyncio
async def test_list_matter_documents_returns_only_bounded_portal_metadata():
    tenant_id = uuid4()
    matter = _matter(tenant_id=tenant_id)
    document = _document(matter_id=matter.id, tenant_id=tenant_id)
    document.provider_object_id = "must-not-leak"
    db = _DB(scalar_values=[matter], result_values=[[document]])

    payload = await workspace.list_matter_documents(
        _context(db, tenant_id),
        ListMatterDocumentsArgs(matter_id=matter.id, category=" pleading ", limit=5),
    )

    assert payload["limit"] == 5
    assert payload["documents"][0]["filename"] == "motion.txt"
    assert "provider_object_id" not in payload["documents"][0]
    assert payload["documents"][0]["download_url"].startswith("/api/matters/")


@pytest.mark.parametrize(
    ("filename", "content_type", "expected"),
    [
        ("evidence.PDF", "application/octet-stream", "pdf"),
        ("draft", "application/pdf", "pdf"),
        ("motion.DOCX", "application/octet-stream", "docx"),
        (
            "draft",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "docx",
        ),
        ("notes.JSON", "application/octet-stream", "text"),
        ("draft", "text/markdown", "text"),
    ],
)
def test_document_format_selection_is_explicit(filename, content_type, expected):
    assert (
        workspace._document_text_format(
            SimpleNamespace(filename=filename, content_type=content_type)
        )
        == expected
    )


@pytest.mark.asyncio
async def test_get_matter_document_text_reads_hashes_and_truncates_bytes(monkeypatch):
    tenant_id = uuid4()
    matter = _matter(tenant_id=tenant_id)
    content = ("admissible evidence " * 20).encode()
    document = _document(
        matter_id=matter.id,
        tenant_id=tenant_id,
        file_size=len(content),
    )
    db = _DB(scalar_values=[matter, document])

    async def read_bytes(_store, **kwargs):
        assert kwargs["tenant_id"] == str(tenant_id)
        assert kwargs["max_bytes"] == workspace._MAX_DOCUMENT_BYTES
        return content

    monkeypatch.setattr(workspace.MatterFileStore, "read_matter_file_bytes", read_bytes)

    payload = await workspace.get_matter_document_text(
        _context(db, tenant_id),
        GetMatterDocumentTextArgs(
            matter_id=matter.id,
            document_id=document.id,
            max_characters=100,
        ),
    )

    assert payload["format"] == "text"
    assert payload["character_count"] == 100
    assert payload["truncated"] is True
    assert payload["page_count"] is None
    assert payload["max_pdf_pages"] is None
    assert payload["content_sha256"] == hashlib.sha256(content).hexdigest()
    assert "untrusted evidence" in payload["content_warning"]


@pytest.mark.asyncio
async def test_get_matter_document_text_maps_storage_failures_without_leaking(
    monkeypatch,
):
    tenant_id = uuid4()
    matter = _matter(tenant_id=tenant_id)
    document = _document(matter_id=matter.id, tenant_id=tenant_id)
    db = _DB(scalar_values=[matter, document])

    async def failed_read(*_args, **_kwargs):
        raise MatterFileReadError("provider token and internal path")

    monkeypatch.setattr(
        workspace.MatterFileStore, "read_matter_file_bytes", failed_read
    )

    with pytest.raises(CapabilityError) as exc_info:
        await workspace.get_matter_document_text(
            _context(db, tenant_id),
            GetMatterDocumentTextArgs(matter_id=matter.id, document_id=document.id),
        )

    assert exc_info.value.code == "document_unavailable"
    assert "provider token" not in exc_info.value.message


@pytest.mark.asyncio
async def test_get_matter_document_text_maps_extractor_failures(monkeypatch):
    tenant_id = uuid4()
    matter = _matter(tenant_id=tenant_id)
    document = _document(matter_id=matter.id, tenant_id=tenant_id)
    db = _DB(scalar_values=[matter, document])

    async def read_bytes(*_args, **_kwargs):
        return b"readable"

    async def broken_extractor(*_args, **_kwargs):
        raise RuntimeError("parser internals")

    monkeypatch.setattr(workspace.MatterFileStore, "read_matter_file_bytes", read_bytes)
    monkeypatch.setattr(workspace.asyncio, "to_thread", broken_extractor)

    with pytest.raises(CapabilityError) as exc_info:
        await workspace.get_matter_document_text(
            _context(db, tenant_id),
            GetMatterDocumentTextArgs(matter_id=matter.id, document_id=document.id),
        )

    assert exc_info.value.code == "document_extraction_failed"
    assert "parser internals" not in exc_info.value.message


@pytest.mark.asyncio
async def test_list_document_templates_filters_and_ranks_automation_ready_matches():
    tenant_id = uuid4()
    matter = _matter(tenant_id=tenant_id)
    compatible = SimpleNamespace(
        id=uuid4(),
        tenant_id=tenant_id,
        title="Discovery Motion",
        description="Firm-approved motion template",
        category="pleading",
        format="docx",
        kind="motion",
        status="approved",
        approved_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        source_sha256="a" * 64,
        source_storage_path="templates/discovery-motion.docx",
        body="",
        jurisdiction="North Dakota",
        stage="discovery",
        module="domestic",
        variable_schema={"properties": {"client_name": {}, "case_number": {}}},
    )
    incompatible = SimpleNamespace(
        **{
            **compatible.__dict__,
            "id": uuid4(),
            "jurisdiction": "Montana",
        }
    )
    draft = SimpleNamespace(
        **{
            **compatible.__dict__,
            "id": uuid4(),
            "status": "draft",
        }
    )
    db = _DB(
        scalar_values=[matter],
        result_values=[[incompatible, draft, compatible]],
    )

    payload = await workspace.list_document_templates(
        _context(db, tenant_id),
        ListDocumentTemplatesArgs(
            matter_id=matter.id,
            query=r"motion%_\\",
            category=" pleading ",
            limit=2,
        ),
    )

    assert payload["recommended_template_id"] == str(compatible.id)
    assert payload["fallback"] == "template_available"
    assert payload["templates"][0]["match_score"] == 15
    assert payload["templates"][0]["match_reasons"] == [
        "jurisdiction",
        "stage",
        "workflow",
        "source-backed",
    ]
    assert payload["templates"][0]["variable_names"] == [
        "case_number",
        "client_name",
    ]


def test_workspace_helpers_fail_closed_on_empty_or_malformed_metadata():
    assert workspace._clip(None) is None
    assert workspace._clip("   ") is None
    assert workspace._bounded_key_dates("not-a-map") == {}
    assert workspace._bounded_key_dates({"": "ignored", "hearing": None}) == {
        "hearing": None
    }
    assert (
        workspace._template_variable_names(SimpleNamespace(variable_schema="invalid"))
        == []
    )
    assert workspace._template_is_compatible(template_value=None, matter_value=None)
    assert not workspace._template_is_compatible(
        template_value="North Dakota", matter_value=None
    )
