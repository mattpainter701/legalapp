"""Reconcile AI field proposals to deterministic template source locations."""

from __future__ import annotations

import copy
import json
import re
from io import BytesIO
from typing import Literal

from docx import Document
from pydantic import BaseModel, ConfigDict, Field

from app.services.docx_templates import (
    docx_source_key,
    iter_docx_paragraphs_with_anchors,
)
from app.services.pdf_templates import (
    _discover_pdf_overlay_fields,
    _inspect_pdf_template,
)
from app.services.template_intake import TemplateAnalysis


class AiFieldProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    existing_name: str | None = Field(default=None, max_length=100)
    name: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=160)
    source_text: str = Field(default="", max_length=200)
    field_type: Literal["text", "multiline", "checkbox"] = "text"
    confidence: float = Field(default=0.5, ge=0, le=1)
    reason: str = Field(default="", max_length=500)


class AiTemplateProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_type: str = Field(default="", max_length=100)
    fields: list[AiFieldProposal] = Field(default_factory=list, max_length=40)
    warnings: list[str] = Field(default_factory=list, max_length=10)


def normalize_ai_field_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")[:100]


def proposal_dict(
    proposal: AiFieldProposal,
    *,
    reason: str | None = None,
) -> dict:
    value = proposal.model_dump()
    if reason:
        value["unmapped_reason"] = reason
    return value


def _source_location_key(field: dict) -> str:
    overlays = field.get("pdf_overlays")
    if not isinstance(overlays, list):
        overlay = field.get("pdf_overlay")
        overlays = [overlay] if isinstance(overlay, dict) else []
    return json.dumps(overlays, sort_keys=True, separators=(",", ":"))


def _candidate_fields(
    proposals: list[AiFieldProposal],
    existing_fields: list[dict],
) -> tuple[list[tuple[AiFieldProposal, str, str]], list[dict]]:
    existing_names = {str(field.get("name") or "").strip() for field in existing_fields}
    existing_sources = {
        str(field.get("source_text") or "").strip()
        for field in existing_fields
        if str(field.get("source_text") or "").strip()
    }
    candidates: list[tuple[AiFieldProposal, str, str]] = []
    unmapped: list[dict] = []
    candidate_names: set[str] = set()
    candidate_sources: set[str] = set()
    for proposal in proposals:
        name = normalize_ai_field_name(proposal.name)
        source_text = proposal.source_text.strip()
        if not name or not source_text:
            unmapped.append(
                proposal_dict(proposal, reason="Exact source text is required.")
            )
            continue
        if name in existing_names or source_text in existing_sources:
            continue
        if name in candidate_names:
            unmapped.append(
                proposal_dict(
                    proposal,
                    reason="The proposed automation key is duplicated.",
                )
            )
            continue
        if source_text in candidate_sources:
            unmapped.append(
                proposal_dict(
                    proposal,
                    reason="The proposed source text is duplicated.",
                )
            )
            continue
        candidate_names.add(name)
        candidate_sources.add(source_text)
        candidates.append((proposal, name, source_text))
    return candidates, unmapped


def _update_existing_fields(
    analysis: TemplateAnalysis,
    proposals: list[AiFieldProposal],
    existing_fields: list[dict],
) -> tuple[list[AiFieldProposal], list[dict], list[dict]]:
    """Apply label/type/name proposals without changing trusted locations."""

    by_name = {
        str(field.get("name") or ""): field
        for field in existing_fields
        if str(field.get("name") or "")
    }
    remaining: list[AiFieldProposal] = []
    updated: list[dict] = []
    unmapped: list[dict] = []
    for proposal in proposals:
        existing_name = str(proposal.existing_name or "").strip()
        if not existing_name:
            remaining.append(proposal)
            continue
        field = by_name.get(existing_name)
        if field is None:
            unmapped.append(
                proposal_dict(
                    proposal,
                    reason="The referenced detected field no longer exists.",
                )
            )
            continue
        next_name = normalize_ai_field_name(proposal.name)
        if not next_name:
            unmapped.append(
                proposal_dict(
                    proposal,
                    reason="The proposed automation key is invalid.",
                )
            )
            continue
        if next_name != existing_name and next_name in by_name:
            unmapped.append(
                proposal_dict(
                    proposal,
                    reason="The proposed automation key is already in use.",
                )
            )
            continue
        field["name"] = next_name
        field["label"] = proposal.label.strip()
        if not field.get("pdf_field_name"):
            field["field_type"] = proposal.field_type
            field["multiline"] = proposal.field_type == "multiline"
        field["ai_suggested"] = True
        field["ai_update_kind"] = "updated"
        field["ai_existing_name"] = existing_name
        field["ai_reason"] = proposal.reason.strip()
        field["ai_confidence"] = round(min(0.75, proposal.confidence), 2)
        if next_name != existing_name:
            placeholder = re.compile(r"\{\{\s*" + re.escape(existing_name) + r"\s*\}\}")
            analysis.body = placeholder.sub(
                "{{" + next_name + "}}",
                analysis.body,
            )
            analysis.body_preview = analysis.body[:2500]
            del by_name[existing_name]
            by_name[next_name] = field
        updated.append(field)
    return remaining, updated, unmapped


def _docx_proposals(
    file_bytes: bytes,
    candidates: list[tuple[AiFieldProposal, str, str]],
) -> tuple[list[dict], list[dict]]:
    document = Document(BytesIO(file_bytes))
    paragraphs = [
        (ordinal, "".join(run.text for run in paragraph.runs))
        for ordinal, paragraph in iter_docx_paragraphs_with_anchors(document)
    ]
    mapped: list[dict] = []
    unmapped: list[dict] = []
    for proposal, name, source_text in candidates:
        occurrences: list[tuple[int, int]] = []
        for ordinal, paragraph_text in paragraphs:
            cursor = 0
            while True:
                start = paragraph_text.find(source_text, cursor)
                if start < 0:
                    break
                occurrences.append((ordinal, start))
                cursor = start + len(source_text)
        if len(occurrences) != 1:
            unmapped.append(
                proposal_dict(
                    proposal,
                    reason=(
                        "Source evidence was not found."
                        if not occurrences
                        else "Source evidence occurs more than once; choose the exact Word location manually."
                    ),
                )
            )
            continue
        ordinal, start = occurrences[0]
        anchor = {
            "paragraph_ordinal": ordinal,
            "start": start,
            "end": start + len(source_text),
        }
        mapped.append(
            {
                "name": name,
                "label": proposal.label.strip(),
                "field_type": proposal.field_type,
                "source_text": source_text,
                "confidence": round(min(0.75, proposal.confidence), 2),
                "review_required": True,
                "ai_suggested": True,
                "ai_update_kind": "added",
                "ai_reason": proposal.reason.strip(),
                "docx_anchor": anchor,
                "docx_source_key": docx_source_key(source_text, anchor),
                "required": False,
            }
        )
    return mapped, unmapped


def _pdf_proposals(
    analysis: TemplateAnalysis,
    file_bytes: bytes,
    candidates: list[tuple[AiFieldProposal, str, str]],
    existing_fields: list[dict],
) -> tuple[list[dict], list[dict]]:
    safe_candidates: list[dict] = []
    proposal_by_name: dict[str, AiFieldProposal] = {}
    unmapped: list[dict] = []
    for proposal, name, source_text in candidates:
        if source_text.endswith(":") or proposal.field_type == "checkbox":
            unmapped.append(
                proposal_dict(
                    proposal,
                    reason="A label-only or checkbox proposal needs a reviewed PDF highlight.",
                )
            )
            continue
        safe_candidates.append(
            {
                "name": name,
                "label": proposal.label.strip(),
                "source_text": source_text,
                "confidence": min(0.75, proposal.confidence),
            }
        )
        proposal_by_name[name] = proposal

    reader, _pdf_fields = _inspect_pdf_template(file_bytes)
    discovered = _discover_pdf_overlay_fields(
        reader,
        safe_candidates,
        fragments=analysis.evidence_fragments,
        merge_native_fragments=bool(analysis.evidence_fragments),
    )
    existing_locations = {
        _source_location_key(field)
        for field in existing_fields
        if _source_location_key(field) != "[]"
    }
    mapped: list[dict] = []
    discovered_names: set[str] = set()
    for field in discovered:
        name = str(field.get("name") or "")
        proposal = proposal_by_name.get(name)
        if proposal is None:
            continue
        location_key = _source_location_key(field)
        if location_key in existing_locations:
            unmapped.append(
                proposal_dict(
                    proposal,
                    reason="That PDF location is already mapped to another field.",
                )
            )
            continue
        field["field_type"] = proposal.field_type
        field["confidence"] = round(
            min(
                0.75,
                float(field.get("confidence") or 0),
                proposal.confidence,
            ),
            2,
        )
        field["review_required"] = True
        field["ai_suggested"] = True
        field["ai_update_kind"] = "added"
        field["ai_reason"] = proposal.reason.strip()
        mapped.append(field)
        discovered_names.add(name)
        existing_locations.add(location_key)
    for proposal, name, _source_text in candidates:
        if name in proposal_by_name and name not in discovered_names:
            unmapped.append(
                proposal_dict(
                    proposal,
                    reason="The server could not locate this evidence safely on a PDF page.",
                )
            )
    return mapped, unmapped


def _text_proposals(
    analysis: TemplateAnalysis,
    candidates: list[tuple[AiFieldProposal, str, str]],
) -> tuple[list[dict], list[dict]]:
    body = analysis.body
    mapped: list[dict] = []
    unmapped: list[dict] = []
    for proposal, name, source_text in candidates:
        if body.count(source_text) != 1:
            unmapped.append(
                proposal_dict(
                    proposal,
                    reason="Source evidence must occur exactly once in the editable body.",
                )
            )
            continue
        body = body.replace(source_text, f"{{{{{name}}}}}", 1)
        mapped.append(
            {
                "name": name,
                "label": proposal.label.strip(),
                "field_type": proposal.field_type,
                "source_text": source_text,
                "confidence": round(min(0.75, proposal.confidence), 2),
                "review_required": True,
                "ai_suggested": True,
                "ai_update_kind": "added",
                "ai_reason": proposal.reason.strip(),
                "required": False,
            }
        )
    analysis.body = body
    analysis.body_preview = body[:2500]
    return mapped, unmapped


def reconcile_ai_template_fields(
    *,
    analysis: TemplateAnalysis,
    file_bytes: bytes,
    proposals: list[AiFieldProposal],
) -> tuple[list[dict], list[dict]]:
    """Accept only proposals with deterministic, non-ambiguous locations."""

    schema = copy.deepcopy(analysis.variable_schema or {})
    existing_fields = [
        copy.deepcopy(field)
        for field in (schema.get("fields") or [])
        if isinstance(field, dict)
    ]
    new_proposals, updated, unmapped = _update_existing_fields(
        analysis,
        proposals,
        existing_fields,
    )
    candidates, candidate_unmapped = _candidate_fields(
        new_proposals,
        existing_fields,
    )
    unmapped.extend(candidate_unmapped)
    if analysis.format == "docx":
        mapped, format_unmapped = _docx_proposals(file_bytes, candidates)
    elif analysis.format == "pdf":
        mapped, format_unmapped = _pdf_proposals(
            analysis,
            file_bytes,
            candidates,
            existing_fields,
        )
    else:
        mapped, format_unmapped = _text_proposals(analysis, candidates)
    unmapped.extend(format_unmapped)
    schema["fields"] = [*existing_fields, *mapped]
    schema["unmapped_ai_suggestions"] = unmapped
    analysis.variable_schema = schema
    return [*updated, *mapped], unmapped
