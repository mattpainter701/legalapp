"""Bounded native parsers used only by the forked parser child."""

from __future__ import annotations

import csv
import io
import json
import re
import subprocess
import tempfile
import zipfile
from email import policy
from email.parser import BytesParser
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any
from xml.etree import ElementTree

from pypdf import PdfReader
from pypdf.errors import FileNotDecryptedError, PdfReadError

from .config import Limits
from .contracts import ExtractionMethod, Section, TerminalStatus


class ParseFailure(Exception):
    def __init__(self, status: TerminalStatus, code: str):
        super().__init__(code)
        self.status = status
        self.code = code


class _TextHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.suppressed = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style"}:
            self.suppressed += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style"} and self.suppressed:
            self.suppressed -= 1

    def handle_data(self, data: str) -> None:
        if not self.suppressed:
            self.parts.append(data)


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _clean(text: str) -> str:
    text = text.replace("\x00", " ").replace("\r\n", "\n")
    return re.sub(r"[ \t]+", " ", text).strip()


def _safe_member(name: str) -> bool:
    path = PurePosixPath(name.replace("\\", "/"))
    return bool(name) and not path.is_absolute() and ".." not in path.parts


class Parser:
    """Parse a single staged file under cumulative archive and output budgets."""

    def __init__(
        self,
        limits: Limits,
        *,
        low_text_chars_per_page: int = 80,
        tika_app_jar: Path | None = None,
        tika_config: Path | None = None,
    ):
        self.limits = limits
        self.output_chars = 0
        self.unpacked_bytes = 0
        self.embedded_files = 0
        self.sections: list[Section] = []
        self.media_type: str | None = None
        self.low_text_chars_per_page = low_text_chars_per_page
        self.ocr_candidate_pages: list[int] = []
        self.tika_app_jar = tika_app_jar
        self.tika_config = tika_config

    def _append(
        self,
        text: str,
        *,
        method: ExtractionMethod = ExtractionMethod.NATIVE,
        page: int | None = None,
        heading: str | None = None,
        source_name: str | None = None,
    ) -> None:
        text = _clean(text)
        if not text:
            return
        encoded = text.encode("utf-8")
        remaining = self.limits.output_bytes - self.output_chars
        if remaining <= 0:
            raise ParseFailure(TerminalStatus.TOO_LARGE, "max-output-bytes")
        if len(encoded) > remaining:
            text = encoded[:remaining].decode("utf-8", errors="ignore")
            if text:
                self.sections.append(
                    Section(
                        ordinal=len(self.sections),
                        text=text,
                        method=method,
                        page_number=page,
                        heading=heading,
                        source_name=source_name,
                    )
                )
            raise ParseFailure(TerminalStatus.TOO_LARGE, "max-output-bytes")
        self.output_chars += len(encoded)
        self.sections.append(
            Section(
                ordinal=len(self.sections),
                text=text,
                method=method,
                page_number=page,
                heading=heading,
                source_name=source_name,
            )
        )

    def parse(self, path: Path) -> tuple[tuple[Section, ...], str | None, tuple[int, ...]]:
        try:
            self._parse_bytes(path.read_bytes(), path.name, depth=0)
        except PermissionError as exc:
            raise ParseFailure(
                TerminalStatus.PERMISSION_DENIED, "source-permission-denied"
            ) from exc
        except ParseFailure:
            raise
        except (
            OSError,
            ValueError,
            ElementTree.ParseError,
            csv.Error,
            json.JSONDecodeError,
        ) as exc:
            raise ParseFailure(TerminalStatus.CORRUPT, "malformed-document") from exc
        return tuple(self.sections), self.media_type, tuple(self.ocr_candidate_pages)

    def _parse_bytes(
        self,
        data: bytes,
        name: str,
        *,
        depth: int,
        method: ExtractionMethod = ExtractionMethod.NATIVE,
    ) -> None:
        suffix = Path(name).suffix.lower()
        if len(data) > self.limits.input_bytes:
            raise ParseFailure(TerminalStatus.TOO_LARGE, "max-input-bytes")
        if suffix in {".txt", ".log", ".md"}:
            self.media_type = self.media_type or "text/plain"
            self._append(_decode(data), method=method, source_name=name)
        elif suffix in {".html", ".htm"}:
            self.media_type = self.media_type or "text/html"
            parser = _TextHtmlParser()
            parser.feed(_decode(data))
            self._append(" ".join(parser.parts), method=method, source_name=name)
        elif suffix == ".xml":
            self.media_type = self.media_type or "application/xml"
            if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
                raise ParseFailure(TerminalStatus.CORRUPT, "xml-doctype-disabled")
            root = ElementTree.fromstring(data)
            self._append(" ".join(root.itertext()), method=method, source_name=name)
        elif suffix == ".csv":
            self.media_type = self.media_type or "text/csv"
            rows = csv.reader(io.StringIO(_decode(data)))
            self._append(
                "\n".join(" | ".join(row) for row in rows), method=method, source_name=name
            )
        elif suffix == ".json":
            self.media_type = self.media_type or "application/json"
            value = json.loads(_decode(data))
            self._append(
                json.dumps(value, ensure_ascii=False, sort_keys=True),
                method=method,
                source_name=name,
            )
        elif suffix in {".eml", ".mime"}:
            self.media_type = self.media_type or "message/rfc822"
            self._parse_email(data, name, depth, method)
        elif suffix == ".rtf":
            self.media_type = self.media_type or "application/rtf"
            source = re.sub(r"\\'[0-9a-fA-F]{2}", " ", _decode(data))
            source = re.sub(r"\\[a-zA-Z]+-?\d* ?", " ", source)
            self._append(re.sub(r"[{}]", " ", source), method=method, source_name=name)
        elif suffix == ".pdf":
            self.media_type = self.media_type or "application/pdf"
            self._parse_pdf(data, name, method)
        elif suffix in {".docx", ".docm", ".xlsx", ".xlsm", ".pptx", ".pptm"}:
            self.media_type = self.media_type or "application/vnd.openxmlformats-officedocument"
            self._parse_ooxml(data, name, method)
        elif suffix in {".zip"}:
            self.media_type = self.media_type or "application/zip"
            self._parse_archive(data, name, depth)
        elif suffix in {".doc", ".xls", ".ppt", ".msg", ".odt", ".ods", ".odp"}:
            self._parse_tika(data, name, method)
        else:
            raise ParseFailure(TerminalStatus.UNSUPPORTED, "unsupported-format")

    def _parse_pdf(self, data: bytes, name: str, method: ExtractionMethod) -> None:
        try:
            reader = PdfReader(io.BytesIO(data), strict=True)
            if reader.is_encrypted:
                raise ParseFailure(TerminalStatus.ENCRYPTED, "encrypted-pdf")
            if len(reader.pages) > self.limits.page_count:
                raise ParseFailure(TerminalStatus.TOO_LARGE, "max-page-count")
            for number, page in enumerate(reader.pages, 1):
                text = page.extract_text() or ""
                if len(text.strip()) < self.low_text_chars_per_page:
                    self.ocr_candidate_pages.append(number)
                self._append(text, method=method, page=number, source_name=name)
        except FileNotDecryptedError as exc:
            raise ParseFailure(TerminalStatus.ENCRYPTED, "encrypted-pdf") from exc
        except PdfReadError as exc:
            raise ParseFailure(TerminalStatus.CORRUPT, "malformed-pdf") from exc

    def _parse_ooxml(self, data: bytes, name: str, method: ExtractionMethod) -> None:
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                infos = archive.infolist()
                self._check_archive_infos(infos)
                interesting = [
                    info
                    for info in infos
                    if info.filename.startswith(("word/", "ppt/slides/", "xl/"))
                    and info.filename.endswith(".xml")
                    and not info.is_dir()
                ]
                for info in sorted(interesting, key=lambda item: item.filename):
                    payload = archive.read(info)
                    if b"<!DOCTYPE" in payload.upper() or b"<!ENTITY" in payload.upper():
                        raise ParseFailure(TerminalStatus.CORRUPT, "xml-doctype-disabled")
                    root = ElementTree.fromstring(payload)
                    text = " ".join(node.text or "" for node in root.iter() if node.text)
                    page = None
                    match = re.search(r"ppt/slides/slide(\d+)\.xml$", info.filename)
                    if match:
                        page = int(match.group(1))
                    self._append(
                        text,
                        method=method,
                        page=page,
                        heading=info.filename,
                        source_name=name,
                    )
        except zipfile.BadZipFile as exc:
            raise ParseFailure(TerminalStatus.CORRUPT, "malformed-ooxml") from exc

    def _check_archive_infos(self, infos: list[zipfile.ZipInfo]) -> None:
        if len(infos) > self.limits.embedded_files:
            raise ParseFailure(TerminalStatus.TOO_LARGE, "max-embedded-files")
        total = sum(info.file_size for info in infos)
        self.unpacked_bytes += total
        if self.unpacked_bytes > self.limits.unpacked_bytes:
            raise ParseFailure(TerminalStatus.TOO_LARGE, "max-unpacked-bytes")
        for info in infos:
            if not _safe_member(info.filename):
                raise ParseFailure(TerminalStatus.CORRUPT, "unsafe-archive-path")
            if info.file_size > self.limits.input_bytes:
                raise ParseFailure(TerminalStatus.TOO_LARGE, "embedded-input-too-large")
            if info.file_size and (
                info.compress_size == 0 or info.file_size / info.compress_size > 100
            ):
                raise ParseFailure(TerminalStatus.TOO_LARGE, "archive-compression-ratio")

    def _parse_archive(self, data: bytes, name: str, depth: int) -> None:
        if depth >= self.limits.archive_depth:
            raise ParseFailure(TerminalStatus.TOO_LARGE, "max-archive-depth")
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                infos = archive.infolist()
                self._check_archive_infos(infos)
                for info in infos:
                    if info.is_dir():
                        continue
                    self.embedded_files += 1
                    if self.embedded_files > self.limits.embedded_files:
                        raise ParseFailure(TerminalStatus.TOO_LARGE, "max-embedded-files")
                    self._parse_bytes(
                        archive.read(info),
                        info.filename,
                        depth=depth + 1,
                        method=ExtractionMethod.EMBEDDED,
                    )
        except zipfile.BadZipFile as exc:
            raise ParseFailure(TerminalStatus.CORRUPT, "malformed-archive") from exc

    def _parse_email(self, data: bytes, name: str, depth: int, method: ExtractionMethod) -> None:
        try:
            message = BytesParser(policy=policy.default).parsebytes(data)
            self._append(
                "\n".join(
                    f"{key}: {message.get(key, '')}" for key in ("Subject", "From", "To", "Date")
                ),
                method=method,
                heading="headers",
                source_name=name,
            )
            for part in message.walk():
                if part.is_multipart():
                    continue
                payload = part.get_payload(decode=True) or b""
                filename = part.get_filename()
                if filename:
                    self.embedded_files += 1
                    if self.embedded_files > self.limits.embedded_files:
                        raise ParseFailure(TerminalStatus.TOO_LARGE, "max-embedded-files")
                    self.unpacked_bytes += len(payload)
                    if self.unpacked_bytes > self.limits.unpacked_bytes:
                        raise ParseFailure(TerminalStatus.TOO_LARGE, "max-unpacked-bytes")
                    self._parse_bytes(
                        payload,
                        filename,
                        depth=depth + 1,
                        method=ExtractionMethod.EMBEDDED,
                    )
                elif part.get_content_type() == "text/html":
                    parser = _TextHtmlParser()
                    parser.feed(_decode(payload))
                    self._append(
                        " ".join(parser.parts),
                        method=method,
                        heading="body",
                        source_name=name,
                    )
                elif part.get_content_maintype() == "text":
                    self._append(_decode(payload), method=method, heading="body", source_name=name)
        except (TypeError, ValueError) as exc:
            raise ParseFailure(TerminalStatus.CORRUPT, "malformed-email") from exc

    def _parse_tika(self, data: bytes, name: str, method: ExtractionMethod) -> None:
        """Use Tika only inside this already resource-bounded parser child."""
        if not self.tika_app_jar or not self.tika_app_jar.is_file():
            raise ParseFailure(TerminalStatus.UNSUPPORTED, "tika-runtime-unavailable")
        suffix = Path(name).suffix
        with tempfile.NamedTemporaryFile(suffix=suffix) as source:
            source.write(data)
            source.flush()
            argv = [
                "java",
                "-Djava.awt.headless=true",
                f"-Xmx{max(128, self.limits.memory_bytes // (2 * 1024 * 1024))}m",
                "-jar",
                str(self.tika_app_jar),
            ]
            if self.tika_config:
                argv.append(f"--config={self.tika_config}")
            argv.extend(["--text", source.name])
            try:
                result = subprocess.run(
                    argv,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    timeout=self.limits.wall_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise ParseFailure(TerminalStatus.TIMED_OUT, "tika-wall-time") from exc
            except FileNotFoundError as exc:
                raise ParseFailure(TerminalStatus.UNSUPPORTED, "java-runtime-unavailable") from exc
        if len(result.stdout) > self.limits.output_bytes:
            raise ParseFailure(TerminalStatus.TOO_LARGE, "max-output-bytes")
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="ignore").lower()
            if "encrypt" in stderr or "password" in stderr:
                raise ParseFailure(TerminalStatus.ENCRYPTED, "encrypted-container")
            raise ParseFailure(TerminalStatus.CORRUPT, "tika-parse-failed")
        self._append(
            result.stdout.decode("utf-8", errors="replace"),
            method=method,
            source_name=name,
        )


def serialize_sections(sections: tuple[Section, ...]) -> list[dict[str, Any]]:
    return [
        {
            "ordinal": item.ordinal,
            "text": item.text,
            "method": item.method.value,
            "page_number": item.page_number,
            "heading": item.heading,
            "source_name": item.source_name,
            "confidence": item.confidence,
        }
        for item in sections
    ]
