import re
from typing import Any, List, Tuple

from app.services.pii_detection import detect_pii, scrub_pii

# PROHIBITED_PHRASES / PHRASE_REPLACEMENTS were removed deliberately. Blind
# substitution of provider and AI terminology corrupts substantive legal work:
# an AI-governance memo or a vendor contract review has to be able to say
# "artificial intelligence", "training", or a named provider. Response
# sanitation is limited to internal prompt tags — see sanitize_response().

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
_LEGAL_RESEARCH_RE = re.compile(
    r"\b(?:case\s+law|citation|court|custod(?:y|ial)|divorce|enforceab(?:le|ility)|"
    r"elements?|governing\s+law|jurisdiction|legal\s+(?:authority|standard|research)|"
    r"precedent|statute|statutory|limitations?\s+period|uccjea|"
    r"binding|non[-\s]?binding|assignment|change[-\s]of[-\s]control|"
    r"material\s+contracts?|board\s+(?:consent|authority|authorization)|"
    r"contract(?:ual)?\s+(?:analysis|review|schedule|provision))\b",
    re.IGNORECASE,
)
_PUBLIC_AUTHORITY_QUESTION_RE = re.compile(
    r"\b(?:case\s+law|citation|courts?|custod(?:y|ial)|divorce|enforceab(?:le|ility)|"
    r"jurisdiction|legal\s+(?:authority|standard|research)|precedent|statute|statutory|"
    r"limitations?\s+period|uccjea)\b",
    re.IGNORECASE,
)
_SUPPLIED_SOURCE_RE = re.compile(
    r"\b(?:attach(?:ed|ment)?|exhibits?|provided|supplied|uploaded)\b",
    re.IGNORECASE,
)
_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*$")
_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)")
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


def consolidate_unverified_model_knowledge(
    text: str, sources: list[dict[str, Any]] | None
) -> tuple[str, int]:
    """Replace repeated model-knowledge tags with one clear source note.

    Per-claim tags are useful while reconciling a response, but they make a
    client-facing answer hard to read when no retrieved source was actually
    cited. Preserve them whenever the answer has a valid retrieved source
    reference; otherwise make the disclosure once and leave the substance
    visibly unverified.
    """
    if not text or not _MODEL_ATTRIBUTION_RE.search(text):
        return text, 0

    known_ids = {
        str(row.get("source_id") or row.get("id") or "").strip().casefold()
        for row in sources or []
    }
    cited_ids = {value.strip().casefold() for value in _SOURCE_REF_RE.findall(text)}
    if known_ids.intersection(cited_ids):
        return text, 0

    cleaned, replacements = _MODEL_ATTRIBUTION_RE.subn("", text)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r" {2,}", " ", cleaned).strip()
    source_note = (
        "**Source note:** The retrieved materials did not substantiate the "
        "analysis below. Treat it as general legal information and verify the "
        "governing jurisdiction's current law before relying on it.\n\n"
    )
    return f"{source_note}{cleaned}", replacements


def requires_retrieved_legal_authority(question: str | None) -> bool:
    """Identify questions where an uncited legal conclusion is unsafe to publish."""
    return bool(_LEGAL_RESEARCH_RE.search(question or ""))


def _substantive_source_units(text: str) -> list[str]:
    """Return prose/list/table units that should carry their own source tag.

    The answer contract tells the model to put one finding per paragraph, list
    item, or schedule row. This parser deliberately enforces that visible
    structure rather than pretending a single citation anywhere proves an
    entire memo. Headings, table separators, and the standard review footer are
    presentation, not findings.
    """
    units: list[str] = []
    for paragraph in re.split(r"\n\s*\n", text or ""):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        lines = [line.strip() for line in paragraph.splitlines() if line.strip()]
        split_lines = len(lines) > 1 and any(
            line.startswith("|") or _LIST_ITEM_RE.match(line) for line in lines
        )
        candidates = lines if split_lines else [" ".join(lines)]
        for index, candidate in enumerate(candidates):
            value = candidate.strip()
            if not value or value.startswith("#") or _TABLE_SEPARATOR_RE.match(value):
                continue
            if value.startswith("---") or "Prepared for" in value:
                continue
            # A Markdown table's first row is a label row when immediately
            # followed by its separator. Data rows remain enforceable units.
            if (
                value.startswith("|")
                and index + 1 < len(candidates)
                and _TABLE_SEPARATOR_RE.match(candidates[index + 1])
            ):
                continue
            plain = re.sub(r"[`*_>#|]", " ", value)
            plain = re.sub(r"\[[^\]]+\]\([^)]*\)", " ", plain)
            words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'-]*", plain)
            # Short labels such as "Board authority" or "Next steps" are
            # headings even when authored as bold text rather than Markdown #.
            if len(words) < 6:
                continue
            units.append(value)
    return units


def _all_substantive_units_are_cited(text: str, eligible_ids: set[str]) -> bool:
    units = _substantive_source_units(text)
    if not units:
        return False
    for unit in units:
        unit_ids = {value.strip().casefold() for value in _SOURCE_REF_RE.findall(unit)}
        if not eligible_ids.intersection(unit_ids):
            return False
    return True


def enforce_legal_citation_integrity(
    question: str | None,
    text: str,
    sources: list[dict[str, Any]] | None,
) -> tuple[str, bool]:
    """Fail closed when a legal-research answer cites no retrieved evidence.

    A disclaimer does not cure an unsupported jurisdiction-specific answer. For
    research questions, require at least one exact source id that was actually
    supplied to the model. The bounded response is intentionally non-substantive:
    it reports the coverage gap without recycling unverified model claims.
    """
    if not requires_retrieved_legal_authority(question):
        return text, False

    source_rows = sources or []
    known_ids = {
        str(row.get("source_id") or row.get("id") or "").strip().casefold()
        for row in source_rows
        if row.get("source_id") or row.get("id")
    }
    cited_ids = {
        value.strip().casefold() for value in _SOURCE_REF_RE.findall(text or "")
    }
    # A fabricated id must not hide behind one valid citation. The frontend has
    # no trustworthy target for it, and rendering a generic "source" badge would
    # make the legal answer look better-supported than it is.
    has_unknown_source = bool(cited_ids.difference(known_ids))

    eligible_ids = known_ids
    needs_public_authority = bool(
        _PUBLIC_AUTHORITY_QUESTION_RE.search(question or "")
        and not _SUPPLIED_SOURCE_RE.search(question or "")
    )
    if needs_public_authority:
        eligible_ids = {
            str(row.get("source_id") or row.get("id") or "").strip().casefold()
            for row in source_rows
            if (
                str(row.get("source_type") or "").casefold() == "public_authority"
                or str(row.get("source") or "").casefold()
                in {
                    "courtlistener_mcp",
                    "legal_authority_mcp",
                    "public_courtlistener",
                }
                or str(row.get("source_id") or row.get("id") or "")
                .strip()
                .casefold()
                .startswith(("authority:", "courtlistener:"))
            )
            and (row.get("source_id") or row.get("id"))
        }

    if (
        eligible_ids.intersection(cited_ids)
        and not has_unknown_source
        and not _MODEL_ATTRIBUTION_RE.search(text or "")
        and _all_substantive_units_are_cited(text or "", eligible_ids)
    ):
        return text, False

    coverage_gap = (
        "## Authority coverage gap\n\n"
        "I couldn't verify a legal answer to this question from a retrieved "
        "statute, rule, case, or supplied document. I won't present an uncited "
        "jurisdiction-specific conclusion or label unrelated material as support. "
        "Each substantive finding, list item, and schedule row must carry its own "
        "retrieved source marker.\n\n"
        "Retry after the public-authority index is available, narrow the "
        "jurisdiction and issue, or attach the controlling sources. Any materials "
        "shown below were retrieved for review but were **not cited** as authority."
    )
    return coverage_gap, True


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
