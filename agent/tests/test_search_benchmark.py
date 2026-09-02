"""The Search Node acceptance runner the operator runbook depends on."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from clarity_agent.search_engine import SearchHit, SearchResponse

RUNNER = (
    Path(__file__).resolve().parents[1] / "benchmarks" / "search_node" / "run_benchmark.py"
)


def _load_runner():
    spec = importlib.util.spec_from_file_location("run_benchmark", RUNNER)
    module = importlib.util.module_from_spec(spec)
    # @dataclass resolves annotations through sys.modules, so register first.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


benchmark = _load_runner()


def response(hits: tuple[SearchHit, ...], total: int) -> SearchResponse:
    return SearchResponse(
        hits=hits,
        total=total,
        took_ms=1,
        timed_out=False,
        engine="opensearch",
        index_schema_version=1,
    )


def hit(document_id: str = "motion-001", page_number: int | None = 7) -> SearchHit:
    return SearchHit(
        document_id=document_id,
        chunk_id=f"{document_id}:{page_number}:0",
        share_id="cases",
        relative_path="Smith/Motion.pdf",
        filename="Motion.pdf",
        extension=".pdf",
        score=1.0,
        snippet="",
        page_number=page_number,
        section_path=("Argument",),
        ordinal=0,
    )


def test_shipped_corpus_loads_with_shared_document_metadata():
    chunks = benchmark.load_documents(benchmark.FIXTURES / "documents.jsonl")
    assert chunks
    by_document: dict[str, list] = {}
    for chunk in chunks:
        by_document.setdefault(chunk.document_id, []).append(chunk)
    # The engine rejects a document whose chunks disagree on generation or
    # metadata, so the fixture loader has to normalize them.
    for group in by_document.values():
        assert len({item.mutation_generation for item in group}) == 1
        assert len({item.content_hash for item in group}) == 1
        assert len({item.acl_tokens for item in group}) == 1
    assert {chunk.chunk_id for chunk in chunks} == {chunk.chunk_id for chunk in chunks}


def test_every_shipped_query_is_evaluable():
    import json

    expectations = json.loads(
        (benchmark.FIXTURES / "queries.json").read_text(encoding="utf-8")
    )
    assert expectations
    for expectation in expectations:
        request = benchmark._request(expectation)
        assert request.query
        assert request.acl_tokens
    # The runbook singles these out; losing either would silently weaken
    # recovery. acl-explicit-deny is the one an allow-only index would fail.
    names = {item["name"] for item in expectations}
    assert "acl-no-allow" in names
    assert "acl-explicit-deny" in names


def test_acl_deny_expectation_fails_when_a_document_leaks():
    expectation = {"name": "acl-explicit-deny", "query": "x", "expected_hits": 0}
    assert benchmark.evaluate(expectation, response((), 0)).passed
    leaked = benchmark.evaluate(expectation, response((hit("private-003", 1),), 1))
    assert not leaked.passed
    assert "expected 0 hits" in leaked.detail


def test_document_and_page_expectations_are_both_enforced():
    expectation = {
        "name": "phrase-page",
        "query": "x",
        "expected_document_id": "motion-001",
        "expected_page_number": 7,
    }
    assert benchmark.evaluate(expectation, response((hit(),), 1)).passed

    wrong_document = benchmark.evaluate(
        expectation, response((hit("contract-002", 7),), 1)
    )
    assert not wrong_document.passed
    assert "contract-002" in wrong_document.detail

    wrong_page = benchmark.evaluate(expectation, response((hit("motion-001", 8),), 1))
    assert not wrong_page.passed
    assert "page 8" in wrong_page.detail

    missing = benchmark.evaluate(expectation, response((), 0))
    assert not missing.passed
    assert "got none" in missing.detail


def test_filters_are_passed_through():
    request = benchmark._request(
        {
            "name": "boolean-section",
            "query": "termination AND breach",
            "acl_tokens": ["benchmark:legal"],
            "filters": {"extensions": [".docx"], "share_ids": ["contracts"]},
        }
    )
    assert request.filters.extensions == (".docx",)
    assert request.filters.share_ids == ("contracts",)
    assert request.filters.matter_ids == ()


def test_empty_corpus_is_rejected(tmp_path: Path):
    empty = tmp_path / "documents.jsonl"
    empty.write_text("\n\n", encoding="utf-8")
    with pytest.raises(ValueError):
        benchmark.load_documents(empty)


def test_authenticated_run_requires_the_password_variable(monkeypatch, capsys):
    monkeypatch.delenv(benchmark.PASSWORD_VARIABLE, raising=False)
    # A missing secret must fail before any connection is attempted, and the
    # secret itself must never be a command-line argument.
    assert benchmark.main(["--url", "https://127.0.0.1:9200"]) == 2
    assert benchmark.PASSWORD_VARIABLE in capsys.readouterr().err


def test_deny_tokens_are_loaded_from_the_corpus():
    """An allow-only loader would silently turn the deny check into a no-op."""
    chunks = benchmark.load_documents(benchmark.FIXTURES / "documents.jsonl")
    denied = [chunk for chunk in chunks if chunk.deny_acl_tokens]
    assert denied, "the corpus no longer exercises an explicit DENY ACE"
    assert denied[0].deny_acl_tokens == ("benchmark:contractor",)
    # The same document is allowed to the group the denied principal belongs to,
    # which is what makes the deny meaningful rather than an absent allow.
    assert "benchmark:everyone" in denied[0].acl_tokens
