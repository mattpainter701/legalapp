"""The FM-05 extraction record to FM-03 document envelope translation."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from clarity_agent.search_ingest import (
    AclNotCaptured,
    document_chunks,
    is_indexable,
)

WHEN = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)


@dataclass(frozen=True)
class Section:
    ordinal: int
    text: str
    page_number: int | None = None
    chunk_id: str = ""
    section_path: tuple[str, ...] = ()
    start_offset: int | None = None
    end_offset: int | None = None


@dataclass(frozen=True)
class Record:
    """Mirrors search_node.contracts.ExtractionRecord structurally."""

    document_id: str = "doc-1"
    share_id: str = "cases"
    relative_path: str = "Smith/Motion.pdf"
    filename: str = "Motion.pdf"
    extension: str = ".pdf"
    content_version: str = "v-abc"
    content_fingerprint: str = "sha-abc"
    status: str = "indexed-ready"
    sections: tuple[Section, ...] = ()
    matter_ids: tuple[str, ...] = ("smith",)
    acl_tokens: tuple[str, ...] = ("sid:S-1-5-21-1",)
    acl_state: str = "healthy"
    provenance: dict = field(default_factory=dict)


def _record(**overrides) -> Record:
    base = Record(
        sections=(
            Section(0, "The movant requests summary judgment.", 7, "c0", ("Argument",), 0, 37),
            Section(1, "The burden shifts to the respondent.", 8, "c1", ("Argument",), 37, 73),
        )
    )
    return replace(base, **overrides)


def test_sections_become_one_atomic_envelope():
    chunks = document_chunks(_record(), modified_at=WHEN, mutation_generation=3)
    assert len(chunks) == 2
    # The engine rejects an envelope whose chunks disagree on any of these.
    assert len({chunk.document_id for chunk in chunks}) == 1
    assert len({chunk.mutation_generation for chunk in chunks}) == 1
    assert len({chunk.content_hash for chunk in chunks}) == 1
    assert len({chunk.acl_tokens for chunk in chunks}) == 1
    assert [chunk.page_number for chunk in chunks] == [7, 8]
    assert [chunk.chunk_id for chunk in chunks] == ["c0", "c1"]
    assert chunks[0].section_path == ("Argument",)
    assert chunks[0].document_version == "v-abc"
    assert chunks[0].content_hash == "sha-abc"
    assert chunks[0].matter_ids == ("smith",)


def test_only_a_terminal_success_is_indexable():
    assert is_indexable(_record())
    for status in ("unsupported", "encrypted", "corrupt", "ocr-failed", "skipped"):
        failed = _record(status=status)
        assert not is_indexable(failed)
        # A classified failure carries no text; publishing it would present an
        # empty document as a searchable one.
        with pytest.raises(ValueError):
            document_chunks(failed, modified_at=WHEN, mutation_generation=1)


def test_an_uncaptured_acl_fails_closed():
    for record in (
        _record(acl_state="pending"),
        _record(acl_state="error"),
        _record(acl_tokens=()),
    ):
        with pytest.raises(AclNotCaptured):
            document_chunks(record, modified_at=WHEN, mutation_generation=1)


def test_deny_tokens_are_carried_onto_every_chunk():
    chunks = document_chunks(
        _record(), modified_at=WHEN, mutation_generation=1, deny_acl_tokens=("sid:S-1-5-32",)
    )
    assert all(chunk.deny_acl_tokens == ("sid:S-1-5-32",) for chunk in chunks)


def test_generation_must_be_positive_because_it_fences_delayed_writes():
    for generation in (0, -1):
        with pytest.raises(ValueError, match="generation"):
            document_chunks(_record(), modified_at=WHEN, mutation_generation=generation)


def test_modified_at_must_be_aware_and_is_normalized_to_utc():
    with pytest.raises(ValueError, match="timezone-aware"):
        document_chunks(
            _record(), modified_at=datetime(2026, 8, 31, 12), mutation_generation=1
        )
    local = datetime(2026, 8, 31, 14, tzinfo=timezone(timedelta(hours=2)))
    chunks = document_chunks(_record(), modified_at=local, mutation_generation=1)
    assert chunks[0].modified_at == WHEN


def test_empty_sections_are_rejected_rather_than_indexed_as_a_blank_document():
    with pytest.raises(ValueError, match="at least one section"):
        document_chunks(_record(sections=()), modified_at=WHEN, mutation_generation=1)
    with pytest.raises(ValueError, match="at least one section"):
        document_chunks(
            _record(sections=(Section(0, ""),)), modified_at=WHEN, mutation_generation=1
        )


def test_a_section_without_a_deterministic_id_still_gets_a_stable_one():
    chunks = document_chunks(
        _record(sections=(Section(4, "text"),)), modified_at=WHEN, mutation_generation=1
    )
    assert chunks[0].chunk_id == "doc-1:4"


@pytest.mark.asyncio
async def test_translated_chunks_are_accepted_by_the_engine_envelope():
    """The point of the adapter: the engine must accept what it produces."""
    from clarity_agent.opensearch_engine import OpenSearchEngine

    engine = OpenSearchEngine("http://127.0.0.1:9200", allow_insecure=True)
    try:
        chunks = document_chunks(
            _record(), modified_at=WHEN, mutation_generation=5, deny_acl_tokens=("sid:deny",)
        )
        source = engine._document_source(chunks)
        assert source["record_type"] == "document"
        assert source["mutation_generation"] == 5
        assert source["acl_tokens"] == ["sid:S-1-5-21-1"]
        assert source["deny_acl_tokens"] == ["sid:deny"]
        assert len(source["chunks"]) == 2
        assert source["chunks"][0]["page_number"] == 7
    finally:
        await engine.close()


def test_the_real_search_node_record_satisfies_this_contract():
    """Guards the seam against drift in the other distribution.

    The adapter consumes the record structurally, so nothing but this test would
    notice if search-node renamed a field. Skipped when that package is not
    installed alongside the agent, which is the normal agent-only checkout.
    """
    import sys

    source = Path(__file__).resolve().parents[2] / "search-node" / "src"
    if not source.is_dir():
        pytest.skip("search-node distribution is not present in this checkout")
    sys.path.insert(0, str(source))
    try:
        from search_node.contracts import ExtractionRecord, Section as NodeSection
        from search_node.contracts import TerminalStatus
    finally:
        sys.path.remove(str(source))

    record = ExtractionRecord(
        schema_version=1,
        job_id="job",
        document_id="doc-1",
        source_id="src",
        file_id="file",
        content_version="v-abc",
        share_id="cases",
        relative_path="Smith/Motion.pdf",
        filename="Motion.pdf",
        extension=".pdf",
        content_fingerprint="sha-abc",
        pipeline_version="1",
        status=TerminalStatus.INDEXED_READY,
        media_type="application/pdf",
        sections=(
            NodeSection(
                ordinal=0,
                text="The movant requests summary judgment.",
                method=__import__(
                    "search_node.contracts", fromlist=["ExtractionMethod"]
                ).ExtractionMethod.NATIVE,
                page_number=7,
                chunk_id="c0",
                section_path=("Argument",),
                start_offset=0,
                end_offset=37,
            ),
        ),
        matter_ids=("smith",),
        acl_tokens=("sid:S-1-5-21-1",),
        acl_state="healthy",
    )
    assert is_indexable(record)
    chunks = document_chunks(record, modified_at=WHEN, mutation_generation=2)
    assert len(chunks) == 1
    assert chunks[0].document_id == "doc-1"
    assert chunks[0].page_number == 7
    assert chunks[0].document_version == "v-abc"

    # The two fields FM-05 does not carry must still be absent, or this
    # adapter's explicit arguments have quietly become redundant.
    assert not hasattr(record, "modified_at")
    assert not hasattr(record, "mutation_generation")
