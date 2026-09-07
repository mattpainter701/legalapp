"""Derive safe, source-backed Word placeholders after intake review.

The retained upload is evidence.  This module creates a separate, deterministic
DOCX for authoring/filling once a reviewer has explicitly mapped a discovered
source span to a field.  Ordinal anchors are used only against the original
bytes, before the derived document is serialized.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from typing import Any, Iterable

from docx import Document

from app.services.docx_source_review import source_review_candidates
from app.services.docx_templates import (
    TemplateDocxError,
    _replace_at_span,
    docx_source_key,
    iter_docx_paragraphs,
    validate_docx_package,
)
from app.services.docx_outline import docx_outline


PLACEHOLDER_DERIVATION_VERSION = 1
SOURCE_MODES = {"prose", "form"}


@dataclass(frozen=True)
class SourceModeSuggestion:
    """A reviewable default; callers must persist an explicit choice."""

    suggested_mode: str
    confidence: float
    signals: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "suggested_mode": self.suggested_mode,
            "confidence": self.confidence,
            "signals": list(self.signals),
        }


@dataclass(frozen=True)
class DerivedDocxSource:
    """Derived source plus the evidence needed to invalidate it safely."""

    content: bytes
    metadata: dict[str, Any]
    variable_schema: dict[str, Any]


def suggest_source_mode(content: bytes) -> SourceModeSuggestion:
    """Suggest form/prose from bounded structural signals.

    This is deliberately only a suggestion.  Publication code should require
    ``resolve_source_mode`` so prose is never silently certified as a form.
    """

    outline = docx_outline(content)
    text = "\n".join(str(item.get("text") or "") for item in outline["paragraphs"])
    blanks = text.count("___")
    labels = sum(
        1
        for line in text.splitlines()
        if ":" in line and len(line.split(":", 1)[0].strip()) <= 50
    )
    signals: list[str] = []
    if blanks:
        signals.append("underscore blanks")
    if labels:
        signals.append("labelled response lines")
    if blanks >= 2 or (blanks and labels >= 2):
        return SourceModeSuggestion(
            "form", 0.9 if blanks >= 2 else 0.75, tuple(signals)
        )
    return SourceModeSuggestion("prose", 0.8, tuple(signals or ["continuous prose"]))


def resolve_source_mode(
    suggestion: SourceModeSuggestion, override: str | None = None
) -> str:
    """Resolve a suggestion only with a valid, explicit optional override."""

    mode = (
        suggestion.suggested_mode if override is None else str(override).strip().lower()
    )
    if mode not in SOURCE_MODES:
        raise TemplateDocxError("Word source mode must be prose or form")
    return mode


def _mapped_review_spans(
    content: bytes, fields: Iterable[dict[str, Any]], decisions: dict[str, str] | None
) -> list[tuple[int, int, int, str, str, str]]:
    """Return exact, explicitly mapped spans as ordinal/start/end/name/source."""

    outline = docx_outline(content)
    candidates, truncated = source_review_candidates(outline["paragraphs"])
    if truncated or outline.get("review_truncated"):
        raise TemplateDocxError("Word source review is truncated; use a smaller source")
    by_id = {item["id"]: item for item in candidates}
    paragraphs = {item["ordinal"]: item["text"] for item in outline["paragraphs"]}
    spans: list[tuple[int, int, int, str, str, str]] = []
    seen: set[tuple[int, int, int]] = set()
    for field in fields:
        if not isinstance(field, dict) or field.get("included") is False:
            continue
        anchor = field.get("docx_anchor")
        source = str(field.get("source_text") or "")
        name = str(field.get("name") or "").strip()
        if not anchor or not source or not name:
            # Authored {{name}} fields already have a literal token and do not
            # need derivation.  Unanchored inferred fields must remain review.
            continue
        try:
            ordinal, start, end = (
                int(anchor["paragraph_ordinal"]),
                int(anchor["start"]),
                int(anchor["end"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise TemplateDocxError(
                "A reviewed Word field has an invalid location"
            ) from exc
        if paragraphs.get(ordinal, "")[start:end] != source or end - start != len(
            source
        ):
            raise TemplateDocxError(
                f"The reviewed source for Word field {name!r} no longer matches"
            )
        candidate_id = docx_source_key(source, anchor)
        # A reviewer may explicitly select arbitrary prose in the authoring
        # view.  Discovery candidates are useful evidence, but are not a
        # prerequisite for an exact user selection.
        decision = (decisions or {}).get(candidate_id)
        if candidate_id in by_id and decision in {
            "fixed",
            "signature",
            "not_applicable",
        }:
            raise TemplateDocxError(
                f"Word field {name!r} conflicts with its source-review disposition"
            )
        key = (ordinal, start, end)
        if key in seen:
            raise TemplateDocxError(
                "Two Word fields cannot map to the same source span"
            )
        seen.add(key)
        spans.append((ordinal, start, end, name, source, candidate_id))
    # Mutate right-to-left inside each paragraph so offsets from the original
    # evidence remain valid when token length differs from source length.
    return sorted(spans, key=lambda item: (item[0], -item[1]))


def derive_reviewed_docx_source(
    original_content: bytes,
    *,
    fields: Iterable[dict[str, Any]],
    decisions: dict[str, str] | None = None,
    source_mode: str = "prose",
    source_mode_suggestion: SourceModeSuggestion | None = None,
) -> DerivedDocxSource:
    """Replace only confirmed exact spans with literal ``{{field}}`` tokens.

    The input is never mutated.  Metadata binds both original and derived
    digests, review version, source mode, and every replacement to its source
    key.  A later source upload invalidates the derivative by digest mismatch.
    """

    validate_docx_package(original_content)
    if source_mode not in SOURCE_MODES:
        raise TemplateDocxError("Word source mode must be prose or form")
    fields = list(fields)
    spans = _mapped_review_spans(original_content, fields, decisions)
    document = Document(io.BytesIO(original_content))
    paragraphs = list(iter_docx_paragraphs(document))
    replacements = []
    for ordinal, start, end, name, source, candidate_id in spans:
        paragraph = paragraphs[ordinal]
        combined = "".join(run.text for run in paragraph.runs)
        if combined[start:end] != source:
            raise TemplateDocxError(
                "The retained Word source changed during derivation"
            )
        if not _replace_at_span(paragraph, start, end, "{{" + name + "}}"):
            raise TemplateDocxError(
                f"Word field {name!r} could not be rewritten safely"
            )
        replacements.append(
            {
                "name": name,
                "source_text": source,
                "source_review_id": candidate_id,
                "anchor": {"paragraph_ordinal": ordinal, "start": start, "end": end},
            }
        )
    output = io.BytesIO()
    document.save(output)
    derived = output.getvalue()
    validate_docx_package(derived)
    metadata = {
        "derivation_version": PLACEHOLDER_DERIVATION_VERSION,
        "source_review_version": 1,
        "source_mode": source_mode,
        "source_mode_suggestion": (
            source_mode_suggestion.as_dict() if source_mode_suggestion else None
        ),
        "original_sha256": hashlib.sha256(original_content).hexdigest(),
        "derived_sha256": hashlib.sha256(derived).hexdigest(),
        "replacements": replacements,
    }
    # Anchors address the retained evidence.  Once rewritten, the active
    # source carries literal tokens and must not retain stale ordinal anchors.
    source_fields = [dict(field) for field in fields if isinstance(field, dict)]
    by_anchor = {
        (ordinal, start, end): name
        for ordinal, start, end, name, _source, _candidate_id in spans
    }
    for field in source_fields:
        anchor = field.get("docx_anchor")
        if not isinstance(anchor, dict):
            continue
        key = (
            int(anchor.get("paragraph_ordinal", -1)),
            int(anchor.get("start", -1)),
            int(anchor.get("end", -1)),
        )
        name = by_anchor.get(key)
        if name:
            field["source_text"] = "{{" + name + "}}"
            field.pop("docx_anchor", None)
            field.pop("docx_source_key", None)
    active_schema = {
        "version": 1,
        "source": "docx_derived_placeholder",
        "fields": source_fields,
        "source_review_version": 1,
        # Candidate IDs are tied to the original source text and offsets. The
        # active outline has new IDs, so decisions must be collected again.
        "source_review": {},
        "source_provenance": metadata,
    }
    return DerivedDocxSource(derived, metadata, active_schema)


def derived_source_is_current(
    original_content: bytes, derived_content: bytes, metadata: dict[str, Any]
) -> bool:
    """Check provenance before using a derived source for preview or filling."""

    return (
        metadata.get("derivation_version") == PLACEHOLDER_DERIVATION_VERSION
        and metadata.get("source_review_version") == 1
        and metadata.get("original_sha256")
        == hashlib.sha256(original_content).hexdigest()
        and metadata.get("derived_sha256")
        == hashlib.sha256(derived_content).hexdigest()
    )
