from pathlib import Path


def test_artifact_approval_verifies_existing_cloud_binding_without_late_upload():
    source = (
        Path(__file__).resolve().parents[1] / "app" / "services" / "task_automation.py"
    ).read_text(encoding="utf-8")
    start = source.index("async def _run_matter_document_draft(")
    end = source.index("async def _recipient_bindings_are_current(", start)
    block = source[start:end]

    verification = block.index("read_matter_file_bytes(")
    integrity = block.index("append_document_integrity_event(")
    approval = block.index('artifact.status = "approved"')

    assert verification < integrity < approval
    assert "generated_artifact_revision_id" in block
    assert 'document.storage_state = "conflict"' in block
    assert "store_matter_file_result(" not in block
    assert "predates verified cloud review" in block
