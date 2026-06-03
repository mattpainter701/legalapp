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


async def extract_text_from_pdf(file_bytes: bytes) -> str:
    """Extract text from PDF bytes using pypdf."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(file_bytes))
    text_parts = []

    for page_num, page in enumerate(reader.pages):
        page_text = page.extract_text()
        if page_text:
            text_parts.append(page_text)

    return "\n\n".join(text_parts)


async def extract_text_from_docx(file_bytes: bytes) -> str:
    """Extract text from DOCX bytes using python-docx."""
    from docx import Document

    doc = Document(io.BytesIO(file_bytes))
    text_parts = []

    for para in doc.paragraphs:
        if para.text.strip():
            text_parts.append(para.text)

    # Also extract text from tables
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                if cell.text.strip():
                    text_parts.append(cell.text)

    return "\n\n".join(text_parts)


async def extract_text(file_bytes: bytes, content_type: str, filename: str) -> str:
    """Route to the correct text extractor based on content type or filename."""
    ct_lower = (content_type or "").lower()
    fn_lower = (filename or "").lower()

    if ct_lower == "application/pdf" or fn_lower.endswith(".pdf"):
        return await extract_text_from_pdf(file_bytes)

    if (
        ct_lower
        in (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/msword",
        )
        or fn_lower.endswith(".docx")
        or fn_lower.endswith(".doc")
    ):
        return await extract_text_from_docx(file_bytes)

    if ct_lower.startswith("text/") or fn_lower.endswith(".txt"):
        return file_bytes.decode("utf-8", errors="replace")

    # Fallback: attempt UTF-8 decode
    try:
        return file_bytes.decode("utf-8", errors="replace")
    except Exception:
        return ""
