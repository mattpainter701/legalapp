"""Supervisor for one-shot parser subprocesses."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path, PurePosixPath

from .config import Settings
from .contracts import (
    ExtractionMethod,
    ExtractionRecord,
    ManifestJob,
    Section,
    TerminalStatus,
    deterministic_chunk_id,
)
from .sandbox import ProcessContainer


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


class IsolatedExtractor:
    def __init__(self, settings: Settings):
        self.settings = settings

    def extract(self, job: ManifestJob) -> ExtractionRecord:
        self.settings.assert_worker_safe()
        path = Path(job.source_path)
        try:
            resolved = path.resolve(strict=True)
            staging_root = self.settings.staging_root.resolve(strict=True)
        except PermissionError:
            return self._terminal(job, TerminalStatus.PERMISSION_DENIED, "source-permission-denied")
        except OSError:
            return self._terminal(job, TerminalStatus.CORRUPT, "source-unavailable")
        if path.is_symlink() or not _inside(resolved, staging_root):
            return self._terminal(job, TerminalStatus.PERMISSION_DENIED, "source-outside-staging")
        try:
            size = resolved.stat().st_size
        except PermissionError:
            return self._terminal(job, TerminalStatus.PERMISSION_DENIED, "source-permission-denied")
        if size != job.size_bytes:
            return self._terminal(job, TerminalStatus.SKIPPED, "source-size-changed")
        if size > self.settings.limits.input_bytes:
            return self._terminal(job, TerminalStatus.TOO_LARGE, "max-input-bytes")

        self.settings.temp_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        workdir = Path(tempfile.mkdtemp(prefix="extract-", dir=self.settings.temp_root))
        try:
            payload = json.dumps(
                {
                    "source_path": str(resolved),
                    "limits": asdict(self.settings.limits),
                    "low_text_chars_per_page": self.settings.low_text_chars_per_page,
                }
            ).encode()
            env = {
                "PATH": os.environ.get("PATH", ""),
                "PYTHONPATH": os.pathsep.join(sys.path),
                "PYTHONIOENCODING": "utf-8",
            }
            tika_jar = os.environ.get("SEARCH_NODE_TIKA_APP_JAR")
            if tika_jar:
                jar_path = Path(tika_jar).resolve(strict=True)
                env["SEARCH_NODE_TIKA_APP_JAR"] = str(jar_path)
            # Tika forks a JVM, so the child is not always the whole tree. The
            # container owns killing every descendant and, on Windows, the only
            # memory and process-count limits the parser gets.
            with ProcessContainer(self.settings, jvm_expected=bool(tika_jar)) as container:
                proc = subprocess.Popen(
                    # cwd is a private empty directory and PYTHONPATH is rebuilt
                    # from the reviewed supervisor process, never from the job.
                    [sys.executable, "-m", "search_node.parser_child"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    cwd=workdir,
                    env=env,
                    start_new_session=False,
                    **container.popen_kwargs(),
                )
                # The child blocks reading stdin until communicate() writes it,
                # so nothing can escape the job between spawn and assignment.
                container.adopt(proc)
                try:
                    stdout, _ = proc.communicate(payload, timeout=self.settings.limits.wall_seconds)
                except subprocess.TimeoutExpired:
                    container.terminate(proc)
                    return self._terminal(job, TerminalStatus.TIMED_OUT, "parser-wall-time")
                if len(stdout) > self.settings.limits.output_bytes + 256 * 1024:
                    return self._terminal(
                        job, TerminalStatus.TOO_LARGE, "parser-envelope-too-large"
                    )
                if proc.returncode != 0:
                    code = (
                        "parser-memory-or-process-limit"
                        if proc.returncode < 0
                        else "parser-process-failed"
                    )
                    return self._terminal(job, TerminalStatus.CORRUPT, code)
                return self._decode(job, stdout)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    def _decode(self, job: ManifestJob, payload: bytes) -> ExtractionRecord:
        try:
            value = json.loads(payload)
            status = TerminalStatus(value["status"])
            decoded_sections: list[Section] = []
            offset = 0
            for item in value.get("sections", []):
                text = str(item["text"])
                ordinal = int(item["ordinal"])
                page_number = item.get("page_number")
                heading = item.get("heading")
                decoded_sections.append(
                    Section(
                        ordinal=ordinal,
                        text=text,
                        method=ExtractionMethod(item["method"]),
                        page_number=page_number,
                        heading=heading,
                        source_name=item.get("source_name"),
                        confidence=item.get("confidence"),
                        chunk_id=deterministic_chunk_id(
                            document_id=job.document_id,
                            content_version=job.content_version,
                            ordinal=ordinal,
                            page_number=page_number,
                            method=ExtractionMethod(item["method"]),
                        ),
                        section_path=(str(heading),) if heading else (),
                        start_offset=offset,
                        end_offset=offset + len(text),
                    )
                )
                offset += len(text)
            sections = tuple(decoded_sections)
            ocr_pages = tuple(int(item) for item in value.get("ocr_candidate_pages", []))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return self._terminal(job, TerminalStatus.CORRUPT, "invalid-parser-envelope")
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
            status=status,
            media_type=value.get("media_type"),
            sections=sections,
            native_text_chars=sum(len(section.text) for section in sections),
            ocr_pending_pages=ocr_pages,
            error_code=value.get("error_code"),
            matter_ids=job.matter_ids,
            provenance={
                "extractor": "lawhand-search-node",
                "isolation": "one-shot-child",
                "network": "disabled-by-runtime-policy",
                "macros": "never-executed",
            },
        )

    @staticmethod
    def _terminal(job: ManifestJob, status: TerminalStatus, code: str) -> ExtractionRecord:
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
            status=status,
            media_type=job.detected_mime,
            error_code=code,
            matter_ids=job.matter_ids,
        )
