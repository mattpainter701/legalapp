from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path
import subprocess

from search_node.config import Limits, Settings
from search_node.contracts import (
    ExtractionMethod,
    ExtractionRecord,
    ManifestJob,
    OcrJob,
    Section,
    TerminalStatus,
)
from search_node.extraction import IsolatedExtractor
from search_node.sandbox import ProcessContainer
from search_node.ocr import OcrRunner, in_off_hours
from search_node.workers import ExtractionWorker, OcrWorker


def settings(tmp_path: Path) -> Settings:
    staging = tmp_path / "staging"
    temp = tmp_path / "temp"
    staging.mkdir(exist_ok=True)
    temp.mkdir(exist_ok=True)
    return Settings(
        enabled=True,
        sandbox_verified=True,
        temp_root=temp,
        staging_root=staging,
        limits=Limits(),
        ocr_languages=("eng",),
        ocr_off_hours_start=20,
        ocr_off_hours_end=6,
        low_text_chars_per_page=80,
    )


def manifest_job(tmp_path: Path, size: int = 1) -> ManifestJob:
    return ManifestJob(
        job_id="job-1",
        document_id="doc-1",
        source_id="source-1",
        file_id="file-1",
        content_version="version-1",
        lease_token="lease-1",
        share_id="share-1",
        source_path=str(tmp_path / "staging" / "document.pdf"),
        relative_path="Matter/document.pdf",
        content_fingerprint="sha256:abc",
        pipeline_version="extract-v1",
        size_bytes=size,
        detected_mime="application/pdf",
    )


def record(job: ManifestJob, *, status=TerminalStatus.INDEXED_READY, ocr_pages=()):
    return ExtractionRecord(
        schema_version=1,
        job_id=job.job_id,
        document_id=job.document_id,
        source_id=job.source_id,
        file_id=job.file_id,
        content_version=job.content_version,
        share_id=job.share_id,
        relative_path=job.relative_path,
        filename="document.pdf",
        extension=".pdf",
        content_fingerprint=job.content_fingerprint,
        pipeline_version=job.pipeline_version,
        status=status,
        media_type="application/pdf",
        sections=(Section(0, "native", ExtractionMethod.NATIVE, page_number=1),),
        native_text_chars=6,
        ocr_pending_pages=ocr_pages,
    )


class Queue:
    def __init__(self, extraction=None, ocr=None):
        self.extraction = extraction
        self.ocr = ocr
        self.events = []

    def lease_extraction(self, *, lease_seconds):
        self.events.append("lease-extraction")
        return self.extraction

    def complete_extraction(self, job, value):
        self.events.append(("complete-extraction", value.status))

    def enqueue_ocr(self, job):
        self.events.append(("enqueue-ocr", job.pages))

    def lease_ocr(self, *, lease_seconds):
        self.events.append("lease-ocr")
        return self.ocr

    def renew_ocr(self, job, *, lease_seconds):
        self.events.append(("renew-ocr", lease_seconds))

    def complete_ocr(self, job, value):
        self.events.append(("complete-ocr", value.status))

    def retry(self, job, *, error_code, retry_after_seconds):
        self.events.append(("retry", job.lease_token, error_code))


class Sink:
    def __init__(self, queue):
        self.queue = queue
        self.records = []

    def publish(self, value):
        self.queue.events.append(("publish", value.provenance.get("revision", "native")))
        self.records.append(value)


class FailingSink(Sink):
    def publish(self, value):
        raise RuntimeError("sink unavailable")


class Extractor:
    def __init__(self, value):
        self.value = value

    def extract(self, job):
        return self.value


class Runner:
    def __init__(self, value):
        self.value = value

    def run(self, job):
        return self.value


def test_native_text_is_published_before_optional_ocr_is_queued(tmp_path: Path):
    job = manifest_job(tmp_path)
    queue = Queue(extraction=job)
    sink = Sink(queue)
    worker = ExtractionWorker(
        settings(tmp_path), queue, sink, Extractor(record(job, ocr_pages=(2,)))
    )
    assert worker.run_once()
    assert queue.events == [
        "lease-extraction",
        ("publish", "native"),
        ("enqueue-ocr", (2,)),
        ("complete-extraction", TerminalStatus.INDEXED_READY),
    ]


def test_terminal_extraction_is_accounted_for_but_not_published(tmp_path: Path):
    job = manifest_job(tmp_path)
    queue = Queue(extraction=job)
    sink = Sink(queue)
    worker = ExtractionWorker(
        settings(tmp_path), queue, sink, Extractor(record(job, status=TerminalStatus.TIMED_OUT))
    )
    assert worker.run_once()
    assert sink.records == []
    assert queue.events[-1] == ("complete-extraction", TerminalStatus.TIMED_OUT)


def test_retry_keeps_the_claim_lease_generation(tmp_path: Path):
    job = manifest_job(tmp_path)
    queue = Queue(extraction=job)
    worker = ExtractionWorker(settings(tmp_path), queue, FailingSink(queue), Extractor(record(job)))
    try:
        worker.run_once()
    except RuntimeError:
        pass
    else:
        raise AssertionError("sink failure was not surfaced")
    assert queue.events[-1] == ("retry", "lease-1", "sink-or-queue-failed")


def test_ocr_enrichment_and_failure_have_explicit_terminal_records(tmp_path: Path):
    base = manifest_job(tmp_path)
    ocr_job = OcrJob(
        job_id=base.job_id,
        document_id=base.document_id,
        source_id=base.source_id,
        file_id=base.file_id,
        content_version=base.content_version,
        lease_token="ocr-lease-1",
        share_id=base.share_id,
        source_path=base.source_path,
        relative_path=base.relative_path,
        content_fingerprint=base.content_fingerprint,
        pipeline_version=base.pipeline_version,
        size_bytes=base.size_bytes,
        matter_ids=base.matter_ids,
        pages=(2,),
        languages=("eng",),
    )
    success = replace(
        record(base),
        sections=(Section(0, "scan", ExtractionMethod.OCR, page_number=2, confidence=0.91),),
        provenance={"revision": "ocr-enrichment"},
    )
    queue = Queue(ocr=ocr_job)
    sink = Sink(queue)
    now = datetime(2026, 8, 31, 21, 0)
    assert OcrWorker(settings(tmp_path), queue, sink, Runner(success)).run_once(now=now)
    assert sink.records[0].sections[0].confidence == 0.91

    failed = OcrRunner._failed(ocr_job, "ocr-page-timeout")
    queue = Queue(ocr=ocr_job)
    sink = Sink(queue)
    assert OcrWorker(settings(tmp_path), queue, sink, Runner(failed)).run_once(now=now)
    assert sink.records == []
    assert queue.events[-1] == ("complete-ocr", TerminalStatus.OCR_FAILED)


def test_off_hours_throttle_handles_overnight_window():
    assert in_off_hours(datetime(2026, 8, 31, 23), 20, 6)
    assert in_off_hours(datetime(2026, 8, 31, 5), 20, 6)
    assert not in_off_hours(datetime(2026, 8, 31, 12), 20, 6)


def test_tesseract_tsv_preserves_mean_word_confidence():
    payload = "level\tconf\ttext\n5\t90\tAlpha\n5\t70\tBeta\n4\t-1\t\n"
    text, confidence = OcrRunner._parse_tsv(payload)
    assert text == "Alpha Beta"
    assert confidence == 0.8


def test_staged_path_and_size_guards_are_terminal(tmp_path: Path):
    config = settings(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("no", encoding="utf-8")
    job = replace(manifest_job(tmp_path, 2), source_path=str(outside))
    result = IsolatedExtractor(config).extract(job)
    assert result.status is TerminalStatus.PERMISSION_DENIED

    staged = config.staging_root / "document.pdf"
    staged.write_bytes(b"123")
    result = IsolatedExtractor(config).extract(manifest_job(tmp_path, 99))
    assert result.status is TerminalStatus.SKIPPED


def test_supervisor_runs_parser_in_one_shot_child(tmp_path: Path):
    config = settings(tmp_path)
    staged = config.staging_root / "document.txt"
    staged.write_text("searchable native text", encoding="utf-8")
    job = replace(
        manifest_job(tmp_path, staged.stat().st_size),
        source_path=str(staged),
        detected_mime="text/plain",
    )
    result = IsolatedExtractor(config).extract(job)
    assert result.status is TerminalStatus.INDEXED_READY
    assert result.sections[0].text == "searchable native text"
    assert result.sections[0].chunk_id
    assert result.sections[0].start_offset == 0
    assert result.sections[0].end_offset == len("searchable native text")
    assert result.file_id == "file-1"
    assert result.content_version == "version-1"
    assert result.provenance["isolation"] == "one-shot-child"


def test_supervisor_classifies_wall_timeout(monkeypatch, tmp_path: Path):
    config = settings(tmp_path)
    staged = config.staging_root / "document.pdf"
    staged.write_bytes(b"x")
    job = manifest_job(tmp_path)

    class TimedOutProcess:
        pid = 123
        returncode = None

        def communicate(self, payload=None, timeout=None):
            if timeout is not None:
                raise subprocess.TimeoutExpired("parser", timeout)
            self.returncode = -9
            return b"", b""

        def kill(self):
            self.returncode = -9

    killed: list[object] = []

    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: TimedOutProcess())
    # This fake is not a real handle/pid; containment itself is covered in
    # test_sandbox.py, so keep both OS calls away from it here.
    monkeypatch.setattr(ProcessContainer, "adopt", lambda self, proc: None)
    monkeypatch.setattr(
        ProcessContainer,
        "terminate",
        lambda self, proc: (killed.append(proc), proc.kill()),
    )
    result = IsolatedExtractor(config).extract(job)
    assert result.status is TerminalStatus.TIMED_OUT
    assert result.error_code == "parser-wall-time"
    # The supervisor must reach for whole-tree containment, not proc.kill().
    assert len(killed) == 1


def test_config_is_default_off(tmp_path: Path):
    config = replace(settings(tmp_path), enabled=False)
    try:
        config.assert_worker_safe()
    except RuntimeError as exc:
        assert "disabled" in str(exc)
    else:
        raise AssertionError("default-off guard did not fail closed")
