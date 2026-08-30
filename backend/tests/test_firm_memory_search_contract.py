"""Focused contracts for the matter-scoped local-search API."""

import pytest
from pydantic import ValidationError

from app.schemas.smb import ContentFetchTask, FirmMemorySearchRequest


def test_local_search_request_trims_and_normalizes_bounds():
    request = FirmMemorySearchRequest(
        query="  indemnification  ",
        matter_id="matter-1",
        file_extensions=[" PDF ", ".PDF", "docx"],
        limit=50,
        correlation_id="run-1",
    )
    assert request.query == "indemnification"
    assert request.file_extensions == [".pdf", ".docx"]


@pytest.mark.parametrize("query", ["", "   ", "x" * 1001])
def test_local_search_rejects_blank_or_oversized_query(query):
    with pytest.raises(ValidationError):
        FirmMemorySearchRequest(query=query, matter_id="matter-1")


def test_local_search_fields_are_optional_for_legacy_tasks():
    task = ContentFetchTask(task_id="legacy", file_path=r"\\server\share\file.pdf")
    assert task.kind == "content_fetch"
    assert task.query is None
    assert task.scopes is None


def test_local_search_task_contract_is_bounded():
    with pytest.raises(ValidationError):
        ContentFetchTask(task_id="x", kind="local_search", query="x" * 1001)
    with pytest.raises(ValidationError):
        ContentFetchTask(task_id="x", kind="local_search", limit=51)
