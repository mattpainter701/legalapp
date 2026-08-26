from __future__ import annotations

import asyncio
import io
import logging
import os
from dataclasses import dataclass

from pypdf import PdfReader
from docx import Document

logger = logging.getLogger("clarity_agent.reader")

STRUCTURED_SOURCE_CAP = 25 * 1024 * 1024
DEFAULT_TEXT_CAP = 512000

_MIME_MAP = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
    ".docm": "application/vnd.ms-word.document.macroEnabled.12",
    ".rtf": "application/rtf",
    ".txt": "text/plain",
}


@dataclass
class ContentResult:
    content: str = ""
    truncated: bool = False
    error: str | None = None


class ExtractionError(ValueError):
    """A file was readable but could not produce usable text."""


class SmbReader:
    async def read_content(
        self,
        session,
        path: str,
        max_bytes: int = DEFAULT_TEXT_CAP,
        max_source_bytes: int = STRUCTURED_SOURCE_CAP,
        connection_kwargs: dict | None = None,
    ) -> ContentResult:
        import smbclient

        try:
            ext = _get_ext(path)
            content, truncated = await asyncio.to_thread(
                _read_content_sync,
                smbclient,
                path,
                ext,
                max_bytes,
                max_source_bytes,
                connection_kwargs or {},
            )
            text = await self.extract_text(path, content)
            if not text.strip():
                raise ExtractionError("No extractable text found in file")
            return ContentResult(
                content=text[:max_bytes], truncated=truncated or len(text) > max_bytes
            )
        except ExtractionError as exc:
            logger.warning("Text extraction unavailable for %s: %s", path, exc)
            return ContentResult(error=str(exc))
        except Exception as exc:
            logger.error("Failed to read %s: %s", path, exc)
            return ContentResult(error=str(exc))

    async def extract_text(self, path: str, content: bytes) -> str:
        return await asyncio.to_thread(_extract_text_sync, path, content)


def _read_content_sync(
    smbclient,
    path: str,
    ext: str,
    max_bytes: int,
    max_source_bytes: int,
    connection_kwargs: dict,
) -> tuple[bytes, bool]:
    with smbclient.open_file(path, mode="rb", **connection_kwargs) as handle:
        if ext in (".pdf", ".docx", ".docm"):
            content = handle.read(max_source_bytes + 1)
            if len(content) > max_source_bytes:
                raise ExtractionError(
                    f"File exceeds structured extraction limit ({max_source_bytes} bytes)"
                )
            return content, False
        content = handle.read(max_bytes + 1)
        truncated = len(content) > max_bytes
        return content[:max_bytes], truncated


def _extract_text_sync(path: str, content: bytes) -> str:
    ext = _get_ext(path)

    if ext == ".pdf":
        try:
            reader = PdfReader(io.BytesIO(content))
            pages = []
            for page in reader.pages:
                t = page.extract_text()
                if t:
                    pages.append(t)
            return "\n".join(pages)
        except Exception as exc:
            logger.warning("PDF extraction failed for %s: %s", path, exc)
            raise ExtractionError(f"PDF extraction failed: {exc}") from exc

    if ext in (".docx", ".docm"):
        try:
            doc = Document(io.BytesIO(content))
            return "\n".join(p.text for p in doc.paragraphs)
        except Exception as exc:
            logger.warning("DOCX extraction failed for %s: %s", path, exc)
            raise ExtractionError(f"DOCX extraction failed: {exc}") from exc

    if ext == ".txt" or ext == ".rtf":
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError:
            return content.decode("latin-1")

    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return content.decode("latin-1")


def _get_ext(path: str) -> str:
    return os.path.splitext(path)[1].lower()
