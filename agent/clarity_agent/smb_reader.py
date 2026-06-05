from __future__ import annotations

import io
import logging
from dataclasses import dataclass

from pypdf import PdfReader
from docx import Document

logger = logging.getLogger("clarity_agent.reader")

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


class SmbReader:
    async def read_content(self, session, path: str, max_bytes: int = 512000) -> ContentResult:
        import smbclient

        try:
            with smbclient.open_file(path, mode="rb") as f:
                content = f.read(max_bytes + 1)
            truncated = len(content) > max_bytes
            content = content[:max_bytes]
            text = await self.extract_text(path, content)
            return ContentResult(content=text, truncated=truncated)
        except Exception as exc:
            logger.error("Failed to read %s: %s", path, exc)
            return ContentResult(error=str(exc))

    async def extract_text(self, path: str, content: bytes) -> str:
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
                return ""

        elif ext in (".docx", ".docm"):
            try:
                doc = Document(io.BytesIO(content))
                return "\n".join(p.text for p in doc.paragraphs)
            except Exception as exc:
                logger.warning("DOCX extraction failed for %s: %s", path, exc)
                return ""

        elif ext == ".txt" or ext == ".rtf":
            try:
                return content.decode("utf-8")
            except UnicodeDecodeError:
                return content.decode("latin-1")

        try:
            return content.decode("utf-8")
        except UnicodeDecodeError:
            return content.decode("latin-1")


def _get_ext(path: str) -> str:
    import os
    return os.path.splitext(path)[1].lower()