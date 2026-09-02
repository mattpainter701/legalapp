"""Separate, bounded OCR execution pool using Poppler and Tesseract CLIs."""

from __future__ import annotations

import csv
import io
import os
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from pathlib import PurePosixPath

from .config import Settings
from .contracts import (
    ExtractionMethod,
    ExtractionRecord,
    OcrJob,
    Section,
    TerminalStatus,
    deterministic_chunk_id,
)
from .extraction import _inside, _posix_limits


class OcrRunner:
    def __init__(self, settings: Settings):
        self.settings = settings

    def run(self, job: OcrJob) -> ExtractionRecord:
        self.settings.assert_worker_safe()
        try:
            source = Path(job.source_path).resolve(strict=True)
            staging = self.settings.staging_root.resolve(strict=True)
            if Path(job.source_path).is_symlink() or not _inside(source, staging):
                return self._failed(job, "source-outside-staging")
        except (OSError, PermissionError):
            return self._failed(job, "source-unavailable")
        if source.suffix.lower() != ".pdf":
            return self._failed(job, "ocr-format-unsupported")
        try:
            current_size = source.stat().st_size
        except (OSError, PermissionError):
            return self._failed(job, "source-unavailable")
        if current_size != job.size_bytes:
            return self._failed(job, "source-size-changed")
        if job.size_bytes > self.settings.limits.input_bytes:
            return self._failed(job, "max-input-bytes")
        if not job.pages or len(job.pages) > self.settings.limits.page_count:
            return self._failed(job, "ocr-page-list-invalid")

        self.settings.temp_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        workdir = Path(tempfile.mkdtemp(prefix="ocr-", dir=self.settings.temp_root))
        sections: list[Section] = []
        try:
            for page_number in sorted(set(job.pages)):
                if page_number < 1:
                    return self._failed(job, "ocr-page-list-invalid")
                prefix = workdir / f"page-{page_number}"
                render = self._command(
                    [
                        "pdftoppm",
                        "-f",
                        str(page_number),
                        "-l",
                        str(page_number),
                        "-singlefile",
                        "-png",
                        "-r",
                        "300",
                        str(source),
                        str(prefix),
                    ]
                )
                if render.returncode != 0:
                    return self._failed(job, "ocr-render-failed")
                image = prefix.with_suffix(".png")
                if (
                    not image.exists()
                    or self._temp_usage(workdir) > self.settings.limits.temp_bytes
                ):
                    return self._failed(job, "ocr-temp-limit")
                tsv = self._command(
                    [
                        "tesseract",
                        str(image),
                        "stdout",
                        "-l",
                        "+".join(job.languages),
                        "tsv",
                    ]
                )
                if tsv.returncode != 0:
                    return self._failed(job, "tesseract-failed")
                if len(tsv.stdout.encode("utf-8")) > self.settings.limits.output_bytes:
                    return self._failed(job, "ocr-output-limit")
                text, confidence = self._parse_tsv(tsv.stdout)
                if text:
                    if (
                        sum(len(item.text.encode("utf-8")) for item in sections)
                        + len(text.encode("utf-8"))
                        > self.settings.limits.output_bytes
                    ):
                        return self._failed(job, "ocr-output-limit")
                    sections.append(
                        Section(
                            ordinal=len(sections),
                            text=text,
                            method=ExtractionMethod.OCR,
                            page_number=page_number,
                            confidence=confidence,
                            chunk_id=deterministic_chunk_id(
                                document_id=job.document_id,
                                content_version=job.content_version,
                                ordinal=len(sections),
                                page_number=page_number,
                                method=ExtractionMethod.OCR,
                            ),
                            start_offset=sum(len(item.text) for item in sections),
                            end_offset=sum(len(item.text) for item in sections) + len(text),
                        )
                    )
                image.unlink(missing_ok=True)
            relative = PurePosixPath(job.relative_path.replace("\\", "/"))
            return ExtractionRecord(
                schema_version=1,
                job_id=job.job_id,
                document_id=job.document_id,
                source_id=job.source_id,
                file_id=job.file_id,
                content_version=job.content_version,
                share_id=job.share_id,
                relative_path=job.relative_path,
                filename=relative.name,
                extension=relative.suffix.lower(),
                content_fingerprint=job.content_fingerprint,
                pipeline_version=job.pipeline_version,
                status=TerminalStatus.INDEXED_READY,
                media_type="application/pdf",
                sections=tuple(sections),
                matter_ids=job.matter_ids,
                provenance={
                    "extractor": "tesseract",
                    "languages": list(job.languages),
                    "page_timeout_seconds": self.settings.limits.ocr_page_seconds,
                    "revision": "ocr-enrichment",
                },
            )
        except subprocess.TimeoutExpired:
            return self._failed(job, "ocr-page-timeout")
        except FileNotFoundError:
            return self._failed(job, "ocr-runtime-unavailable")
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    def _command(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        env = {"PATH": os.environ.get("PATH", ""), "OMP_THREAD_LIMIT": "1"}
        return subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=self.settings.limits.ocr_page_seconds,
            check=False,
            env=env,
            preexec_fn=_posix_limits(self.settings),
        )

    @staticmethod
    def _parse_tsv(payload: str) -> tuple[str, float | None]:
        words: list[str] = []
        confidences: list[float] = []
        for row in csv.DictReader(io.StringIO(payload), delimiter="\t"):
            word = (row.get("text") or "").strip()
            if not word:
                continue
            words.append(word)
            try:
                confidence = float(row.get("conf", "-1"))
            except ValueError:
                continue
            if confidence >= 0:
                confidences.append(confidence / 100)
        return " ".join(words), (sum(confidences) / len(confidences) if confidences else None)

    @staticmethod
    def _temp_usage(root: Path) -> int:
        return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())

    @staticmethod
    def _failed(job: OcrJob, code: str) -> ExtractionRecord:
        relative = PurePosixPath(job.relative_path.replace("\\", "/"))
        return ExtractionRecord(
            schema_version=1,
            job_id=job.job_id,
            document_id=job.document_id,
            source_id=job.source_id,
            file_id=job.file_id,
            content_version=job.content_version,
            share_id=job.share_id,
            relative_path=job.relative_path,
            filename=relative.name,
            extension=relative.suffix.lower(),
            content_fingerprint=job.content_fingerprint,
            pipeline_version=job.pipeline_version,
            status=TerminalStatus.OCR_FAILED,
            media_type="application/pdf",
            error_code=code,
            matter_ids=job.matter_ids,
            provenance={"revision": "ocr-enrichment"},
        )


def in_off_hours(now: datetime, start_hour: int, end_hour: int) -> bool:
    hour = now.hour
    if start_hour == end_hour:
        return True
    if start_hour < end_hour:
        return start_hour <= hour < end_hour
    return hour >= start_hour or hour < end_hour
