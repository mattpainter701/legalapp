import re
import uuid

from app.models.document import Document
from scripts.repair_cybersafe_atlas_chat_sources import SOURCE_TAG_RE, _answer, _source


def test_atlas_demo_answer_has_known_inline_sources_and_cited_tracker_rows():
    source_ids = {
        "document:loi",
        "document:schedule",
        "document:board",
    }
    answer = _answer(
        loi_id="document:loi",
        schedule_id="document:schedule",
        board_id="document:board",
    )

    assert set(SOURCE_TAG_RE.findall(answer)) == source_ids
    assert answer.count("[source:") >= 20

    tracker_rows = [
        line
        for line in answer.splitlines()
        if line.startswith("|")
        and not line.startswith("|---")
        and "Contract / item" not in line
    ]
    assert len(tracker_rows) == 8
    assert all("[source:" in row for row in tracker_rows)
    assert all("[verify]" in row for row in tracker_rows)


def test_demo_document_source_uses_authenticated_download_and_pinpoint():
    document = Document(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        filename="01_Project_Atlas_Letter_of_Intent.docx",
        status="ready",
        chunk_count=0,
    )

    source = _source(
        document,
        title="Project Atlas Letter of Intent",
        locator="Sections 1–9",
        excerpt="Synthetic excerpt",
    )

    assert source["source_id"] == f"document:{document.id}"
    assert source["url"] == f"/api/documents/{document.id}/download"
    assert source["locator"] == "Sections 1–9"
    assert re.fullmatch(r"document:[0-9a-f-]{36}", source["source_id"])
