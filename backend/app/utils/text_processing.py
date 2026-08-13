import io
from typing import List

import tiktoken


def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> List[str]:
    """
    Chunk text into overlapping token-based segments using tiktoken.
    Uses the cl100k_base encoding (compatible with text-embedding-3-small).
    """
    if not text or not text.strip():
        return []

    enc = tiktoken.get_encoding("cl100k_base")
    tokens = enc.encode(text)

    if len(tokens) <= chunk_size:
        return [text]

    chunks: List[str] = []
    start = 0

    while start < len(tokens):
        end = min(start + chunk_size, len(tokens))
        chunk_tokens = tokens[start:end]
        chunk_text_decoded = enc.decode(chunk_tokens)
        chunks.append(chunk_text_decoded)

        if end == len(tokens):
            break

        # Move forward by (chunk_size - overlap) tokens
        start += chunk_size - overlap

    return chunks


def extract_text_from_pdf(
    file_bytes: bytes,
    *,
    max_pages: int | None = None,
    max_chars: int | None = None,
) -> str:
    """Extract text from PDF bytes using pypdf."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(file_bytes))
    text_parts = []
    extracted_chars = 0

    for page_num, page in enumerate(reader.pages):
        if max_pages is not None and page_num >= max_pages:
            break
        page_text = page.extract_text()
        if page_text:
            if max_chars is not None:
                remaining = max_chars - extracted_chars
                if remaining <= 0:
                    break
                page_text = page_text[:remaining]
            text_parts.append(page_text)
            extracted_chars += len(page_text)
            if max_chars is not None and extracted_chars >= max_chars:
                break

    extracted = "\n\n".join(text_parts)
    return extracted[:max_chars] if max_chars is not None else extracted


def extract_text_from_docx(file_bytes: bytes) -> str:
    """Extract text from DOCX bytes using python-docx."""
    from docx import Document

    doc = Document(io.BytesIO(file_bytes))
    text_parts: list[str] = []
    seen_paragraphs: set[int] = set()

    def add_paragraphs(paragraphs) -> None:
        for para in paragraphs:
            marker = id(para._p)
            if marker in seen_paragraphs:
                continue
            seen_paragraphs.add(marker)
            if para.text.strip():
                text_parts.append(para.text.strip())

    def add_tables(tables) -> None:
        for table in tables:
            for row in table.rows:
                values = [cell.text.strip() for cell in row.cells]
                nonblank = [value for value in values if value]
                if len(values) == 2 and values[0] and values[1]:
                    text_parts.append(f"{values[0].rstrip(':')}: {values[1]}")
                elif nonblank:
                    text_parts.append(" | ".join(nonblank))
                for cell in row.cells:
                    add_tables(cell.tables)

    add_paragraphs(doc.paragraphs)
    add_tables(doc.tables)
    for section in doc.sections:
        for container in (section.header, section.footer):
            add_paragraphs(container.paragraphs)
            add_tables(container.tables)

    return "\n\n".join(text_parts)


def extract_text(
    file_bytes: bytes,
    content_type: str,
    filename: str,
    *,
    max_pdf_pages: int | None = None,
    max_pdf_chars: int | None = None,
) -> str:
    """Route to the correct text extractor based on content type or filename."""
    ct_lower = (content_type or "").lower()
    fn_lower = (filename or "").lower()

    if ct_lower == "application/msword" or fn_lower.endswith(".doc"):
        raise ValueError(
            "Legacy .doc files are not supported; convert to DOCX, PDF, or TXT."
        )

    if ct_lower == "application/pdf" or fn_lower.endswith(".pdf"):
        return extract_text_from_pdf(
            file_bytes, max_pages=max_pdf_pages, max_chars=max_pdf_chars
        )

    if (
        ct_lower
        in (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        or fn_lower.endswith(".docx")
    ):
        return extract_text_from_docx(file_bytes)

    if ct_lower.startswith("text/") or fn_lower.endswith(".txt"):
        return file_bytes.decode("utf-8", errors="replace")

    # Fallback: attempt UTF-8 decode
    try:
        return file_bytes.decode("utf-8", errors="replace")
    except Exception:
        return ""
