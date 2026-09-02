"""Queue orchestration with native-first publishing and later OCR enrichment."""

from __future__ import annotations

from datetime import datetime

from .config import Settings
from .contracts import ManifestQueue, OcrJob, SearchSink, TerminalStatus
from .extraction import IsolatedExtractor
from .ocr import OcrRunner, in_off_hours


class ExtractionWorker:
    def __init__(
        self,
        settings: Settings,
        queue: ManifestQueue,
        sink: SearchSink,
        extractor: IsolatedExtractor | None = None,
    ) -> None:
        self.settings = settings
        self.queue = queue
        self.sink = sink
        self.extractor = extractor or IsolatedExtractor(settings)

    def run_once(self) -> bool:
        self.settings.assert_worker_safe()
        job = self.queue.lease_extraction(lease_seconds=self.settings.limits.wall_seconds + 30)
        if job is None:
            return False
        record = self.extractor.extract(job)
        try:
            if record.status is TerminalStatus.INDEXED_READY:
                # This acknowledgement intentionally happens before OCR is queued.
                self.sink.publish(record)
                if record.ocr_pending_pages:
                    self.queue.enqueue_ocr(
                        OcrJob(
                            job_id=job.job_id,
                            document_id=job.document_id,
                            source_id=job.source_id,
                            file_id=job.file_id,
                            content_version=job.content_version,
                            lease_token="",
                            share_id=job.share_id,
                            source_path=job.source_path,
                            relative_path=job.relative_path,
                            content_fingerprint=job.content_fingerprint,
                            pipeline_version=job.pipeline_version,
                            size_bytes=job.size_bytes,
                            matter_ids=job.matter_ids,
                            pages=record.ocr_pending_pages,
                            languages=self.settings.ocr_languages,
                        )
                    )
            self.queue.complete_extraction(job, record)
        except Exception:
            self.queue.retry(job, error_code="sink-or-queue-failed", retry_after_seconds=30)
            raise
        return True


class OcrWorker:
    def __init__(
        self,
        settings: Settings,
        queue: ManifestQueue,
        sink: SearchSink,
        runner: OcrRunner | None = None,
    ) -> None:
        self.settings = settings
        self.queue = queue
        self.sink = sink
        self.runner = runner or OcrRunner(settings)

    def run_once(self, *, now: datetime | None = None) -> bool:
        self.settings.assert_worker_safe()
        now = now or datetime.now().astimezone()
        if not in_off_hours(
            now, self.settings.ocr_off_hours_start, self.settings.ocr_off_hours_end
        ):
            return False
        job = self.queue.lease_ocr(lease_seconds=self.settings.limits.ocr_page_seconds + 30)
        if job is None:
            return False
        self.queue.renew_ocr(
            job,
            lease_seconds=len(job.pages) * self.settings.limits.ocr_page_seconds + 30,
        )
        record = self.runner.run(job)
        try:
            if record.status is TerminalStatus.INDEXED_READY:
                self.sink.publish(record)
            self.queue.complete_ocr(job, record)
        except Exception:
            self.queue.retry(job, error_code="ocr-sink-or-queue-failed", retry_after_seconds=60)
            raise
        return True
