"""Clause-level chunking for legal documents.

Replaces fixed-size 500-token tokenizer chunking with structure-aware splitting
that respects legal document anatomy: sections, articles, numbered clauses,
defined terms, and whereas clauses.

Each chunk carries metadata (section_path, clause_type) that the retrieval layer
can use for clause-type-aware reranking and filtering.
"""

import re
from dataclasses import dataclass

import tiktoken

# ── Section / clause boundary patterns ──────────────────────────────────────

# Section headers: "SECTION 1", "Section 1.01", "ARTICLE I", "Article 1"
SECTION_RE = re.compile(
    r"^\s*(?:SECTION|Section|section)\s+\d+[\.\-\s]|"
    r"^\s*(?:ARTICLE|Article|article)\s+[IVXLCDM\d]+|"
    r"^\s*§\s*\d+",
    re.MULTILINE,
)

# Numbered clauses: "1.1", "1.1.1", "(a)", "(i)", "a)", "i."
CLAUSE_RE = re.compile(
    r"^\s*(?:\d+\.)+\d*\s+|"  # 1.1, 1.1.1
    r"^\s*\([a-z]\)\s+|"  # (a), (b)
    r"^\s*\([ivxlcdm]+\)\s+|"  # (i), (ii), (iv)
    r"^\s*[a-z]\.\s+|"  # a., b.
    r"^\s*[ivxlcdm]+\.\s+",  # i., ii.
    re.MULTILINE,
)

# Definition markers: "means", "shall mean", "refers to", "is defined as"
DEFINITION_RE = re.compile(
    r"\b(?:means|shall mean|refers to|is defined as|shall be construed as)\b",
    re.IGNORECASE,
)

# Obligation markers: "shall", "must", "will", "agrees to", "covenants to"
OBLIGATION_RE = re.compile(
    r"\b(?:shall|must|will|agrees to|covenants to|is required to|undertakes to)\b",
    re.IGNORECASE,
)

# Remedy markers
REMEDY_RE = re.compile(
    r"\b(?:remedy|damages|specific performance|injunction|indemnif|liquidated damages)\b",
    re.IGNORECASE,
)

# Governing law / jurisdiction markers
GOVERNING_LAW_RE = re.compile(
    r"\b(?:governing law|jurisdiction|venue|choice of law|forum selection)\b",
    re.IGNORECASE,
)

# Whereas / recital markers
WHEREAS_RE = re.compile(r"^\s*WHEREAS[,\s]", re.MULTILINE | re.IGNORECASE)

# ── Clause type classification ──────────────────────────────────────────────

CLAUSE_TYPE_KEYWORDS: list[tuple[str, "re.Pattern", str]] = [
    ("definition", DEFINITION_RE, "defined"),
    ("obligation", OBLIGATION_RE, "obligation"),
    ("remedy", REMEDY_RE, "remedy"),
    ("governing_law", GOVERNING_LAW_RE, "governing_law"),
    ("recital", WHEREAS_RE, "recital"),
]

# Minimum tokens per chunk — if a clause is shorter than this, merge with next
MIN_CHUNK_TOKENS = 80
# Target max tokens — prefer splitting at clause boundaries below this
TARGET_MAX_TOKENS = 800
# Hard max — never exceed (splits mid-sentence if absolutely required)
HARD_MAX_TOKENS = 1200
# Overlap tokens between chunks (carried from previous chunk end)
OVERLAP_TOKENS = 40


@dataclass
class LegalChunk:
    content: str
    section_path: str = ""
    clause_type: str = "general"
    chunk_index: int = 0

    def to_dict(self) -> dict:
        return {
            "content": self.content,
            "section_path": self.section_path,
            "clause_type": self.clause_type,
            "chunk_index": self.chunk_index,
        }


def _classify_clause(text: str) -> str:
    """Return the best-matching clause_type for a block of text."""
    scores: dict[str, int] = {}
    for ctype, pattern, _keyword in CLAUSE_TYPE_KEYWORDS:
        count = len(pattern.findall(text))
        if count:
            scores[ctype] = count
    if not scores:
        return "general"
    # Return the type with the most keyword hits
    return max(scores, key=scores.get)


def _token_count(text: str, enc: tiktoken.Encoding) -> int:
    return len(enc.encode(text))


def _detect_boundaries(text: str) -> list[int]:
    """Find section and clause boundary positions in the text.

    Returns sorted list of character offsets where a new logical unit begins.
    """
    boundaries: set[int] = {0}

    for pattern in (SECTION_RE, CLAUSE_RE):
        for m in pattern.finditer(text):
            boundaries.add(m.start())

    # Also add paragraph breaks (double newline) as soft boundaries
    for m in re.finditer(r"\n\s*\n", text):
        boundaries.add(m.start())

    return sorted(boundaries)


def _build_section_path(text: str, chunk_start: int, boundaries: list[int]) -> str:
    """Derive a section_path for a chunk by walking back through boundaries."""
    path_parts: list[str] = []
    for b in boundaries:
        if b > chunk_start:
            break
        # Sniff the line at each boundary for a header
        line_end = text.find("\n", b)
        if line_end == -1:
            line_end = len(text)
        line = text[b:line_end].strip()
        if SECTION_RE.match(line) or CLAUSE_RE.match(line):
            # Clean up for path display
            clean = re.sub(r"\s+", " ", line).strip(" .:")
            if clean and len(clean) < 100:
                path_parts.append(clean)
    return " > ".join(path_parts) if path_parts else ""


def chunk_legal_text(
    text: str,
    target_max_tokens: int = TARGET_MAX_TOKENS,
    hard_max_tokens: int = HARD_MAX_TOKENS,
    min_chunk_tokens: int = MIN_CHUNK_TOKENS,
    overlap_tokens: int = OVERLAP_TOKENS,
) -> list[LegalChunk]:
    """Split legal text into clause-aware chunks.

    Strategy (priority order):
    1. Split at section/article boundaries
    2. Within sections, split at clause boundaries
    3. Within clauses, split at paragraph breaks
    4. Fall back to token-boundary split if a single clause exceeds hard_max
    """
    if not text or not text.strip():
        return []

    enc = tiktoken.get_encoding("cl100k_base")
    boundaries = _detect_boundaries(text)

    chunks: list[LegalChunk] = []
    current_start = 0
    current_end = 0
    chunk_idx = 0

    while current_start < len(text):
        # Scan forward through boundaries until we hit target_max
        found_boundary = False
        for b in boundaries:
            if b <= current_start:
                continue
            seg_tokens = _token_count(text[current_start:b], enc)
            if seg_tokens > hard_max_tokens:
                break  # too far — need to split before this
            if seg_tokens >= target_max_tokens:
                current_end = b
                found_boundary = True
                break
            # Track last viable boundary below target
            if seg_tokens >= min_chunk_tokens:
                current_end = b
                found_boundary = True

        # If we found a good boundary, use it
        if found_boundary and current_end > current_start:
            content = text[current_start:current_end].strip()
        else:
            # Fall back to hard token split
            tokens = enc.encode(text[current_start:])
            if len(tokens) <= hard_max_tokens:
                content = text[current_start:].strip()
                current_end = len(text)
            else:
                hard_tokens = tokens[:hard_max_tokens]
                content = enc.decode(hard_tokens)
                # Try to break at last sentence boundary
                last_period = max(
                    content.rfind(". "),
                    content.rfind(".\n"),
                    content.rfind("; "),
                )
                if last_period > hard_max_tokens // 2:
                    content = content[: last_period + 1]
                current_end = current_start + len(enc.encode(content))

        if content:
            clause_type = _classify_clause(content)
            section_path = _build_section_path(text, current_start, boundaries)
            chunks.append(
                LegalChunk(
                    content=content,
                    section_path=section_path,
                    clause_type=clause_type,
                    chunk_index=chunk_idx,
                )
            )
            chunk_idx += 1

        # Advance start, carrying overlap
        if current_end >= len(text):
            break

        # Find overlap start: rewind by overlap_tokens from current_end
        overlap_start = max(
            current_start,
            current_end - _token_count(content[-overlap_tokens * 4 :], enc)
            if len(content) > overlap_tokens * 4
            else current_end,
        )
        # Walk to nearest sentence boundary for clean overlap
        overlap_text = text[overlap_start:current_end]
        sentence_break = max(
            overlap_text.rfind(". "),
            overlap_text.rfind(".\n"),
            overlap_text.rfind("\n\n"),
        )
        if sentence_break > 0:
            current_start = overlap_start + sentence_break + 1
        else:
            current_start = current_end

    return chunks


def chunk_legal_document(text: str) -> list[dict]:
    """Convenience wrapper: chunk and return dicts for DB insertion."""
    return [c.to_dict() for c in chunk_legal_text(text)]
