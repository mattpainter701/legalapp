import re
from typing import Any, List, Tuple

from app.services.pii_detection import detect_pii, scrub_pii

PROHIBITED_PHRASES = [
    "as an ai",
    "as an language model",
    "i am an ai",
    "i'm an ai",
    "deepseek",
    "claude",
    "gpt",
    "openai",
    "anthropic",
    "language model",
    "large language model",
    "llm",
    "artificial intelligence",
]

# Replacements for prohibited phrases
PHRASE_REPLACEMENTS = {
    "as an ai": "as a legal research assistant",
    "as an language model": "as a legal research assistant",
    "i am an ai": "I am a legal research assistant",
    "i'm an ai": "I am a legal research assistant",
    "deepseek": "the legal research system",
    "claude": "the legal research system",
    "gpt": "the legal research system",
    "openai": "the legal research provider",
    "anthropic": "the legal research provider",
    "language model": "legal research system",
    "large language model": "legal research system",
    "llm": "legal research system",
    "artificial intelligence": "legal research technology",
}

INTERNAL_CONTEXT_TAG_RE = re.compile(
    r"\[\s*FIRM\s+CONTEXT\s*(?::\s*([^\]]+?))?\s*\]",
    re.IGNORECASE,
)

# Legal citation pattern: Smith v. Jones, 123 F.3d 456, (2023), No. 22-1234
CITATION_PATTERN = re.compile(
    r"""
    (
        \b\w[\w\s,\.]+\s+v\.\s+\w[\w\s,\.]+  # Case name: X v. Y
        |
        \d+\s+[A-Z][a-zA-Z\.]+\s+\d+          # Reporter: 123 F.3d 456
        |
        \(\d{4}\)                               # Year: (2023)
        |
        No\.\s+\d{2}-\d+                        # Docket: No. 22-1234
    )
    """,
    re.VERBOSE,
)


def check_prohibited_phrases(text: str) -> bool:
    """Returns True if any prohibited phrases are found (case-insensitive)."""
    text_lower = text.lower()
    return any(phrase in text_lower for phrase in PROHIBITED_PHRASES)


def sanitize_response(text: str) -> str:
    """Remove internal prompt tags without rewriting substantive content.

    Provider and AI terminology can be legally material (for example in an AI
    governance memo or vendor contract).  Rewriting those words corrupts the
    answer, so response sanitation is intentionally limited to internal tags.
    """
    return sanitize_internal_context_tags(text)


def sanitize_internal_context_tags(text: str) -> str:
    """Rewrite old/internal prompt source tags into user-safe provenance tags."""

    def repl(match: re.Match) -> str:
        label = (match.group(1) or "").strip()
        if label:
            return f"[cited by context: {label}]"
        return "[cited by context]"

    return INTERNAL_CONTEXT_TAG_RE.sub(repl, text)


def check_has_citation(text: str) -> bool:
    """Check if response contains a legal citation pattern."""
    return bool(CITATION_PATTERN.search(text))


def check_pii_in_input(text: str) -> List[dict]:
    """Check for PII in user input. Returns list of findings."""
    return detect_pii(text)


def prepare_provider_text(text: str | None, privacy_mode: bool) -> str:
    """Return text safe to cross an external model-provider boundary."""
    value = text or ""
    return scrub_pii(value) if privacy_mode else value


def prepare_provider_messages(
    messages: list[dict[str, Any]], privacy_mode: bool
) -> list[dict[str, Any]]:
    """Copy and scrub every textual message sent to an external provider."""
    if not privacy_mode:
        return [dict(message) for message in messages]
    prepared: list[dict[str, Any]] = []
    for message in messages:
        item = dict(message)
        if isinstance(item.get("content"), str):
            item["content"] = scrub_pii(item["content"])
        prepared.append(item)
    return prepared


_SETTLED_TAG_RE = re.compile(r"\[settled\]", re.IGNORECASE)
_SOURCE_REF_RE = re.compile(r"\[source:\s*([^\]]+)\]", re.IGNORECASE)
_QUOTED_SPAN_RE = re.compile(r'["“]([^"”]{20,})["”]')


_MODEL_ATTRIBUTION_RE = re.compile(
    r"\[(?:model\s+knowledge|model\s+reasoning)\]", re.IGNORECASE
)


def reconcile_retrieved_source_attribution(
    text: str, sources: list[dict[str, Any]] | None
) -> tuple[str, int]:
    """Attach retrieved sources when a model mislabeled a matching citation.

    This is deliberately narrow: a model attribution is changed only when the
    immediately preceding claim contains a distinctive case/source name or
    citation that exactly matches a retrieved result. The claim remains
    ``[verify]`` unless it independently satisfies the stricter settled check.
    """
    candidates: list[tuple[str, tuple[str, ...]]] = []
    for row in sources or []:
        source_id = str(row.get("source_id") or row.get("id") or "").strip()
        if not source_id:
            continue
        identifiers = []
        for key in ("citation", "case_name", "title"):
            value = re.sub(r"\s+", " ", str(row.get(key) or "")).strip().casefold()
            if len(value) >= 8 and value not in {"unknown case", "legal authority"}:
                identifiers.append(value)
        if identifiers:
            candidates.append((source_id, tuple(identifiers)))

    if not candidates or not text:
        return text, 0

    reconciled = 0

    def replace(match: re.Match) -> str:
        nonlocal reconciled
        claim = text[max(0, match.start() - 700) : match.start()]
        previous_tag = max(
            claim.casefold().rfind("[verify]"),
            claim.casefold().rfind("[model knowledge]"),
            claim.casefold().rfind("[model reasoning]"),
            claim.casefold().rfind("[settled]"),
        )
        if previous_tag >= 0:
            claim = claim[previous_tag:]
        normalized_claim = re.sub(r"\s+", " ", claim).casefold()
        existing_ids = {
            value.strip().casefold() for value in _SOURCE_REF_RE.findall(claim)
        }
        has_valid_existing_source = any(
            source_id.casefold() in existing_ids for source_id, _ in candidates
        )
        matched_ids = []
        for source_id, identifiers in candidates:
            if source_id.casefold() in existing_ids:
                continue
            if any(identifier in normalized_claim for identifier in identifiers):
                matched_ids.append(source_id)
        if not matched_ids and not has_valid_existing_source:
            return match.group(0)
        reconciled += 1
        refs = " ".join(f"[source: {source_id}]" for source_id in matched_ids[:3])
        return f"{refs} [verify]" if refs else "[verify]"

    return _MODEL_ATTRIBUTION_RE.sub(replace, text), reconciled


def validate_citation_confidence(
    text: str, sources: list[dict[str, Any]] | None
) -> tuple[str, int]:
    """Downgrade unsupported ``[settled]`` labels to ``[verify]``.

    Retrieval alone is not treated as proof.  A settled label survives only
    when it explicitly references a retrieved source id with ``[source: <id>]``
    and includes a material quoted span found in that source's excerpt. Merely
    retrieving a source or repeating its citation is never treated as proof.
    """
    source_rows = sources or []
    sources_by_id = {
        str(row.get("id") or row.get("source_id")).strip().casefold(): row
        for row in source_rows
        if row.get("id") or row.get("source_id")
    }

    downgraded = 0

    def replace(match: re.Match) -> str:
        nonlocal downgraded
        # Legal citations themselves contain periods, so sentence splitting is
        # unsafe.  Inspect a bounded claim window before the confidence tag.
        claim = text[max(0, match.start() - 500) : match.start()]
        previous_tag = max(
            claim.lower().rfind("[verify]"),
            claim.lower().rfind("[model knowledge]"),
            claim.lower().rfind("[settled]"),
        )
        if previous_tag >= 0:
            claim = claim[previous_tag:]
        explicit_ids = {
            value.strip().casefold() for value in _SOURCE_REF_RE.findall(claim)
        }
        quoted_spans = [
            re.sub(r"\s+", " ", value).strip().casefold()
            for value in _QUOTED_SPAN_RE.findall(claim)
        ]
        span_supported = False
        for source_id in explicit_ids:
            row = sources_by_id.get(source_id)
            if not row:
                continue
            source_text = re.sub(
                r"\s+",
                " ",
                str(row.get("excerpt") or row.get("content") or ""),
            ).casefold()
            if any(span in source_text for span in quoted_spans):
                span_supported = True
                break
        if span_supported:
            return match.group(0)
        downgraded += 1
        return "[verify]"

    return _SETTLED_TAG_RE.sub(replace, text), downgraded


def apply_guardrails(
    text: str, privacy_mode: bool = False
) -> Tuple[str, bool, List[dict]]:
    """
    Apply all guardrails to a response.
    Returns (cleaned_text, needs_retry, pii_findings).
    - cleaned_text: guardrails applied
    - needs_retry: True if contaminated with AI self-disclosure
    - pii_findings: list of PII detected in text
    """
    cleaned = sanitize_internal_context_tags(text)
    # Substantive provider/AI terminology must remain intact.  Internal tag
    # cleanup is deterministic and no longer needs a model retry.
    needs_retry = False

    # Check for PII in output (especially in privacy mode)
    pii_findings = detect_pii(cleaned) if privacy_mode else []
    if pii_findings and privacy_mode:
        cleaned = scrub_pii(cleaned)

    return cleaned, needs_retry, pii_findings
