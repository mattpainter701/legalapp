from pathlib import Path


def test_legacy_document_approval_is_blocked_instead_of_uploading_late():
    source = Path("backend/app/services/task_automation.py").read_text(encoding="utf-8")
    start = source.index("async def _run_matter_document_draft(")
    end = source.index("async def _recipient_bindings_are_current(", start)
    block = source[start:end]

    assert "store_matter_file_result(" not in block
    assert "predates verified cloud review" in block
    assert "Regenerate it" in block
    assert "pending_document.artifact_id is None" in source
    assert "pending_document.document_id is None" in source
